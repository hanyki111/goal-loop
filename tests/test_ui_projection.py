import unittest

from goal_loop_tool import ContractDraft
from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.service import GoalLoop
from goal_loop_tool.ui_projection import goal_graph_projection


class UiProjectionTests(unittest.TestCase):
    def test_empty_graph_is_versioned_and_json_ready(self):
        projection = goal_graph_projection(GoalLoop())
        self.assertEqual(projection, {"schema_version": 16, "project_root": None, "aims": [], "nodes": [], "edges": [], "evidence": [], "proposals": [], "attempts": [], "goals": [], "verdicts": [], "evidence_corrections": [], "impact_reviews": [], "decisions": [], "aim_evaluations": [], "ao_runs": [], "memory_mentions": [], "memory_documents": {}, "memory_drafts": []})

    def test_detail_records_support_aim_lanes_and_attempt_timeline_without_graph_noise(self):
        loop = GoalLoop()
        aim = loop.create_aim("UI timeline")
        proposal = loop.create_proposal(aim.id, "Build the view")
        goal = loop.accept_proposal(
            proposal.id,
            GoalIdentity(aim.id, "A visible timeline", "produce"),
            ContractDraft("local UI", "timeline is readable", "reviewer checks it"),
            idempotency_key="ui-projection-detail",
        )
        attempt = loop.start_attempt(goal.id, 1, "executor", "render timeline")
        loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = loop.record_evidence(attempt.id, "observer", "timeline appeared")
        loop.begin_verification(goal.id, attempt.id)
        verdict = loop.submit_verdict(goal.id, attempt.id, "reviewer", VerdictResult.SATISFIED, [evidence.id])

        projection = goal_graph_projection(loop)

        self.assertEqual([node["kind"] for node in projection["nodes"]], ["aim", "goal"])
        self.assertEqual(projection["aims"][0]["description"], "UI timeline")
        self.assertEqual(projection["goals"][0]["current_contract_version"], 1)
        self.assertEqual(projection["attempts"][0]["executor"], "executor")
        self.assertEqual(projection["evidence"][0]["attempt_id"], attempt.id)
        self.assertEqual(projection["proposals"][0]["aim_id"], aim.id)
        self.assertEqual(projection["verdicts"][0]["id"], verdict.id)

    def test_goal_contract_history_lists_every_version_oldest_first(self):
        loop = GoalLoop()
        aim = loop.create_aim("Criteria history")
        proposal = loop.create_proposal(aim.id, "Measure it")
        goal = loop.accept_proposal(
            proposal.id,
            GoalIdentity(aim.id, "A measurable guide", "produce"),
            ContractDraft("start screen", "users find the next step", "manual QA"),
            idempotency_key="ui-projection-history",
        )
        loop.revise_contract(goal.id, ContractDraft("start screen", "users find the next step in 30 seconds", "manual QA"), decided_by_actor_id="owner", reason="make it measurable")

        record = goal_graph_projection(loop)["goals"][0]

        self.assertEqual(record["current_contract_version"], 2)
        self.assertEqual([item["version"] for item in record["contract_history"]], [1, 2])
        self.assertEqual(record["contract_history"][0]["success_criterion"], "users find the next step")
        self.assertEqual(record["contract"], record["contract_history"][-1])

    def test_retry_decisions_are_exposed_with_reason_and_consumer(self):
        loop = GoalLoop()
        aim = loop.create_aim("Retry trail")
        proposal = loop.create_proposal(aim.id, "Try twice")
        goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "A retried task", "produce"), ContractDraft("scope", "criterion", "verification"), idempotency_key="ui-projection-retry")
        first = loop.start_attempt(goal.id, 1, "executor", "first try")
        loop.complete_attempt(first.id, AttemptOutcome.FAILED)
        decision = loop.decide_same_contract_retry(goal.id, first.id, "owner", "fix the input order")
        second = loop.start_attempt(goal.id, 1, "executor", "second try", retry_decision_id=decision.id)

        record = goal_graph_projection(loop)["decisions"][0]

        self.assertEqual(record["reason"], "fix the input order")
        self.assertEqual(record["target_attempt_id"], first.id)
        self.assertEqual(record["consumed_by_attempt_id"], second.id)

    def test_evidence_corrections_and_impact_reviews_are_exposed(self):
        loop = GoalLoop()
        aim = loop.create_aim("Correction trail")
        proposal = loop.create_proposal(aim.id, "Observe carefully")
        goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "A corrected task", "produce"), ContractDraft("scope", "criterion", "verification"), idempotency_key="ui-projection-correction")
        attempt = loop.start_attempt(goal.id, 1, "executor", "run")
        loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = loop.record_evidence(attempt.id, "observer", "screen showed the guide")
        loop.begin_verification(goal.id, attempt.id)
        verdict = loop.submit_verdict(goal.id, attempt.id, "reviewer", VerdictResult.SATISFIED, [evidence.id])
        loop.correct_evidence(evidence.id, "observer", "the screen was an older build")

        projection = goal_graph_projection(loop)

        self.assertEqual(projection["evidence_corrections"][0]["corrected_evidence_id"], evidence.id)
        self.assertEqual(projection["evidence_corrections"][0]["observation"], "the screen was an older build")
        self.assertEqual(projection["impact_reviews"][0]["trigger_verdict_id"], verdict.id)
        self.assertIsInstance(projection["impact_reviews"][0]["affected_goal_ids"], list)

