output "state_machine_arn" {
  value = aws_sfn_state_machine.analysis.arn
}

output "pull_function_name" {
  value = aws_lambda_function.pull.function_name
}

output "analyze_function_name" {
  value = aws_lambda_function.analyze.function_name
}
