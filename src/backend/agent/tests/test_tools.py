from __future__ import annotations

import unittest

from prompt import SYSTEM_PROMPT
from tools import get_findings, job_status, start_analysis


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, Item):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        pk = values[":pk"]
        prefix = values[":sk"]
        rows = [item for (item_pk, item_sk), item in self.items.items() if item_pk == pk and item_sk.startswith(prefix)]
        return {"Items": rows}


class FakeSfn:
    def __init__(self) -> None:
        self.started = []

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"executionArn": "arn:aws:states:us-east-1:1:execution:demo/" + kwargs["name"]}


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = FakeTable()
        self.sfn = FakeSfn()
        self.clients = {"table": self.table, "sfn": self.sfn, "state_machine_arn": "arn:aws:states:us-east-1:1:stateMachine:demo"}

    def test_start_analysis_records_a_queued_job_and_starts_the_state_machine(self):
        result = start_analysis("FDA-2025-N-3262-0001", self.clients)
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(len(self.sfn.started), 1)
        self.assertEqual(job_status(result["jobId"], self.clients)["status"], "QUEUED")

    def test_get_findings_filters_industry_and_form_letters(self):
        job_id = "job-1"
        self.table.put_item(Item={"pk": f"JOB#{job_id}", "sk": "META", "jobId": job_id, "status": "SUCCEEDED", "formLetterComments": 12, "uniqueComments": 4, "documentId": "FDA-1"})
        self.table.put_item(Item={"pk": f"DOC#FDA-1", "sk": "LATEST", "jobId": job_id, "documentId": "FDA-1"})
        self.table.put_item(
            Item={
                "pk": f"JOB#{job_id}",
                "sk": "FINDING#form-a",
                "findingId": "form-a",
                "theme": "finalize the rule",
                "stance": "support",
                "commenterType": "advocacy",
                "summary": "template",
                "clusterSize": 12,
                "isFormLetter": True,
                "representativeIds": ["c1"],
            }
        )
        self.table.put_item(
            Item={
                "pk": f"JOB#{job_id}",
                "sk": "FINDING#unique-d",
                "findingId": "unique-d",
                "theme": "device software",
                "stance": "oppose",
                "commenterType": "industry",
                "summary": "exclude logs",
                "clusterSize": 1,
                "isFormLetter": False,
                "representativeIds": ["d"],
            }
        )
        industry = get_findings(document_id="FDA-1", commenter_type="industry", clients=self.clients)
        self.assertEqual([row["findingId"] for row in industry["findings"]], ["unique-d"])
        self.assertEqual(industry["formLetterComments"], 12)
        letters = get_findings(job_id=job_id, form_letters_only=True, clients=self.clients)
        self.assertEqual([row["findingId"] for row in letters["findings"]], ["form-a"])

    def test_prompt_is_written_for_an_analyst(self):
        self.assertIn("legal and compliance analyst", SYSTEM_PROMPT)
        self.assertIn("Do not invent counts", SYSTEM_PROMPT)
