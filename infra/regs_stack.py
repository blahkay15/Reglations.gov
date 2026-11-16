"""One stack for the comment workspace.

Context keys (all optional unless noted):

- vpcMode: ``create`` (default) or ``import``
- createNat: ``true`` to add a NAT gateway and private subnets. Default is
  public subnets and a public IP on Fargate, which avoids the NAT charge.
- vpcId, subnetIds, availabilityZones: required when vpcMode is import.
  subnetIds is a comma-separated list. Set assignPublicIp to ``false`` when
  those subnets are private and already have a path to the internet.
- domainName, hostedZoneId, hostedZoneName: together these attach a Route 53
  alias. Leave them unset to use the CloudFront domain only.
- certificateArn: an existing us-east-1 certificate. Required when the stack
  is not in us-east-1 and a domain name is set. CloudFront only accepts
  certificates from us-east-1.
- cognitoDomainPrefix: prefix for the Cognito hosted UI. Must be unique in
  the region.
- schedule: EventBridge Scheduler expression. Default is 08:00 America/New_York
  on weekdays.
- workerImage: full image URI. Default is this account's
  regs-comments-worker:latest repository, which the pipeline pushes after
  the first deploy creates it.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_authorizers as authz,
    aws_apigatewayv2_integrations as integrations,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_s3 as s3,
    aws_scheduler as scheduler,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


def _text(scope: Construct, key: str, default: str = "") -> str:
    value = scope.node.try_get_context(key)
    if value is None:
        return default
    return str(value).strip()


def _flag(scope: Construct, key: str, default: bool = False) -> bool:
    value = scope.node.try_get_context(key)
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


class RegsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        domain_name = _text(self, "domainName")
        zone_id = _text(self, "hostedZoneId")
        zone_name = _text(self, "hostedZoneName")
        certificate_arn = _text(self, "certificateArn")
        if domain_name and not (zone_id and zone_name):
            raise ValueError("domainName requires hostedZoneId and hostedZoneName, or omit the domain.")
        if domain_name and self.region != "us-east-1" and not certificate_arn:
            raise ValueError(
                "CloudFront certificates must live in us-east-1. Deploy this stack in us-east-1, "
                "or pass -c certificateArn=arn:aws:acm:us-east-1:...:certificate/..."
            )

        vpc, subnet_selection, assign_public_ip = self._vpc()
        data_bucket = s3.Bucket(
            self,
            "JobData",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        site_bucket = s3.Bucket(
            self,
            "Site",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
        )
        api_key = secretsmanager.Secret(
            self,
            "RegsApiKey",
            secret_name="regs-comments/api-key",
            description="regulations.gov API key. Put the value in after deploy. A raw string or {\"apiKey\": \"...\"} both work.",
        )
        jobs = self._table("Jobs", "jobId")
        watches = self._table("Watches", "documentId")
        repository = ecr.Repository(
            self,
            "WorkerRepo",
            repository_name="regs-comments-worker",
            removal_policy=RemovalPolicy.RETAIN,
        )
        image_tag = _text(self, "workerImageTag", "latest")
        cluster, task_definition, task_security_group = self._worker(
            vpc, data_bucket, jobs, watches, api_key, repository, image_tag
        )
        code = lambda_.Code.from_asset("build/lambda")
        common_env = {
            "JOB_TABLE": jobs.table_name,
            "WATCH_TABLE": watches.table_name,
            "DATA_BUCKET": data_bucket.bucket_name,
            "API_KEY_SECRET_ARN": api_key.secret_arn,
            "ECS_CLUSTER": cluster.cluster_name,
            "TASK_DEFINITION": task_definition.task_definition_arn,
            "SUBNET_IDS": Fn.join(",", subnet_selection.subnet_ids),
            "TASK_SECURITY_GROUP": task_security_group.security_group_id,
            "ASSIGN_PUBLIC_IP": "ENABLED" if assign_public_ip else "DISABLED",
        }

        api_fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.handler.lambda_handler",
            code=code,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=common_env,
        )
        watcher = lambda_.Function(
            self,
            "WatcherFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="api.watcher.lambda_handler",
            code=code,
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=common_env,
        )
        self._grant_data(api_fn, jobs, watches, data_bucket, api_key, task_definition)
        jobs.grant_read_write_data(task_definition.task_role)
        watches.grant_read_write_data(task_definition.task_role)
        data_bucket.grant_read_write(task_definition.task_role)
        api_key.grant_read(task_definition.task_role)
        watches.grant_read_write_data(watcher)
        api_key.grant_read(watcher)
        watcher.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:regs-comments/teams/*"],
            )
        )

        user_pool = cognito.UserPool(
            self,
            "Users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(email=cognito.StandardAttribute(required=True, mutable=True)),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
        )
        prefix = _text(self, "cognitoDomainPrefix", "regs-comments-dev")
        user_pool.add_domain("HostedUi", cognito_domain=cognito.CognitoDomainOptions(domain_prefix=prefix))

        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_headers=["authorization", "content-type"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_origins=["*"],
            ),
        )
        distribution = self._distribution(site_bucket, http_api, domain_name, zone_id, zone_name, certificate_arn)
        callback_urls = [
            "http://localhost:5173/",
            f"https://{distribution.distribution_domain_name}/",
        ]
        if domain_name:
            callback_urls.append(f"https://{domain_name}/")
        client = user_pool.add_client(
            "Web",
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=callback_urls,
                logout_urls=callback_urls,
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            auth_flows=cognito.AuthFlow(user_password=True),
        )
        authorizer = authz.HttpJwtAuthorizer(
            "CognitoJwt",
            f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[client.user_pool_client_id],
        )
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("Api", api_fn),
            authorizer=authorizer,
        )
        self._schedule(watcher, distribution, domain_name)

        site_url = f"https://{domain_name}" if domain_name else f"https://{distribution.distribution_domain_name}"
        CfnOutput(self, "SiteUrl", value=site_url)
        CfnOutput(self, "DistributionUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        CfnOutput(self, "FrontendBucket", value=site_bucket.bucket_name)
        CfnOutput(self, "DataBucket", value=data_bucket.bucket_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "CognitoHostedUi", value=f"https://{prefix}.auth.{self.region}.amazoncognito.com")
        CfnOutput(self, "ApiKeySecretArn", value=api_key.secret_arn)
        CfnOutput(self, "WorkerRepository", value=repository.repository_uri)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)

    def _table(self, name: str, sort_key: str) -> dynamodb.Table:
        return dynamodb.Table(
            self,
            name,
            partition_key=dynamodb.Attribute(name="userId", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name=sort_key, type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

    def _vpc(self) -> tuple[ec2.IVpc, ec2.SelectedSubnets, bool]:
        mode = _text(self, "vpcMode", "create") or "create"
        if mode == "import":
            vpc_id = _text(self, "vpcId")
            subnet_ids = [item.strip() for item in _text(self, "subnetIds").split(",") if item.strip()]
            zones = [item.strip() for item in _text(self, "availabilityZones").split(",") if item.strip()]
            if not vpc_id or not subnet_ids or not zones:
                raise ValueError("vpcMode=import requires -c vpcId, -c subnetIds, and -c availabilityZones.")
            public = _flag(self, "assignPublicIp", True)
            vpc = ec2.Vpc.from_vpc_attributes(
                self,
                "ImportedVpc",
                vpc_id=vpc_id,
                availability_zones=zones,
                public_subnet_ids=subnet_ids if public else None,
                private_subnet_ids=None if public else subnet_ids,
            )
            selected = vpc.select_subnets(
                subnet_type=ec2.SubnetType.PUBLIC if public else ec2.SubnetType.PRIVATE_WITH_EGRESS
            )
            return vpc, selected, public

        private = _flag(self, "createNat", False)
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=1 if private else 0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                *(
                    [ec2.SubnetConfiguration(name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24)]
                    if private
                    else []
                ),
            ],
        )
        if private:
            return vpc, vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS), False
        return vpc, vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC), True

    def _worker(self, vpc, data_bucket, jobs, watches, api_key, repository, image_tag: str):
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        security_group = ec2.SecurityGroup(self, "WorkerSg", vpc=vpc, allow_all_outbound=True, description="Fargate comment puller")
        task_role = iam.Role(self, "WorkerRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))
        execution_role = iam.Role(
            self,
            "WorkerExecution",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
        )
        task = ecs.FargateTaskDefinition(
            self,
            "WorkerTask",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=task_role,
            execution_role=execution_role,
        )
        task.add_container(
            "worker",
            image=ecs.ContainerImage.from_ecr_repository(repository, image_tag),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="worker"),
            environment={
                "JOB_TABLE": jobs.table_name,
                "WATCH_TABLE": watches.table_name,
                "DATA_BUCKET": data_bucket.bucket_name,
                "API_KEY_SECRET_ARN": api_key.secret_arn,
                "CLUSTER_THRESHOLD": _text(self, "clusterThreshold", "0.8"),
                "CLUSTER_MIN_SIZE": _text(self, "clusterMinSize", "3"),
            },
        )
        return cluster, task, security_group

    def _grant_data(self, api_fn, jobs, watches, data_bucket, api_key, task_definition) -> None:
        jobs.grant_read_write_data(api_fn)
        watches.grant_read_write_data(api_fn)
        data_bucket.grant_read_write(api_fn)
        api_key.grant_read(api_fn)
        api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:GetSecretValue"],
                resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:regs-comments/teams/*"],
            )
        )
        api_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["ecs:RunTask"], resources=[task_definition.task_definition_arn])
        )
        api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[task_definition.task_role.role_arn, task_definition.execution_role.role_arn],
            )
        )

    def _distribution(self, site_bucket, http_api, domain_name, zone_id, zone_name, certificate_arn):
        site_origin = origins.S3BucketOrigin.with_origin_access_control(site_bucket)
        api_origin = origins.HttpOrigin(
            f"{http_api.api_id}.execute-api.{self.region}.amazonaws.com",
        )
        certificate = None
        zone = None
        if domain_name:
            zone = route53.HostedZone.from_hosted_zone_attributes(
                self, "Zone", hosted_zone_id=zone_id, zone_name=zone_name
            )
            if certificate_arn:
                certificate = cloudfront_cert(certificate_arn, self)
            else:
                from aws_cdk import aws_certificatemanager as acm

                certificate = acm.Certificate(
                    self,
                    "SiteCert",
                    domain_name=domain_name,
                    validation=acm.CertificateValidation.from_dns(zone),
                )
        distribution = cloudfront.Distribution(
            self,
            "Cdn",
            default_root_object="index.html",
            domain_names=[domain_name] if domain_name else None,
            certificate=certificate,
            default_behavior=cloudfront.BehaviorOptions(
                origin=site_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/config.json": cloudfront.BehaviorOptions(
                    origin=site_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                ),
                "/api/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                ),
            },
        )
        if zone is not None:
            record_name = None if domain_name == zone_name else domain_name
            route53.ARecord(
                self,
                "SiteAlias",
                zone=zone,
                record_name=record_name,
                target=route53.RecordTarget.from_alias(targets.CloudFrontTarget(distribution)),
            )
        return distribution

    def _schedule(self, watcher: lambda_.Function, distribution, domain_name: str) -> None:
        role = iam.Role(self, "ScheduleRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        watcher.grant_invoke(role)
        expression = _text(self, "schedule", "cron(0 8 ? * MON-FRI *)")
        app_url = f"https://{domain_name}" if domain_name else f"https://{distribution.distribution_domain_name}"
        watcher.add_environment("APP_URL", app_url)
        scheduler.CfnSchedule(
            self,
            "WeekdayWatch",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression=expression,
            schedule_expression_timezone="America/New_York",
            target=scheduler.CfnSchedule.TargetProperty(arn=watcher.function_arn, role_arn=role.role_arn),
        )

def cloudfront_cert(certificate_arn: str, stack: Stack):
    from aws_cdk import aws_certificatemanager as acm

    return acm.Certificate.from_certificate_arn(stack, "ImportedCert", certificate_arn)
