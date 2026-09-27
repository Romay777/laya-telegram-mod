"""Seam 1: the HTTP contract of the app's ASGI interface.

A `choice` request with `model: "multilingual"` against `POST /v1/systemone`
returns `answers.<q>.probabilities` containing every label of the question spec
(spam, ads, insult, clean), and `GET /health` answers.
"""

SPAM_MESSAGE = {"message": "Buy cheap crypto now, DM me", "urls": ["https://t.me/+abc"]}


class TestSystemOneChoice:
    def test_choice_returns_probabilities_for_every_label(
        self, client, question_spec, spec_labels
    ):
        response = client.post(
            "/v1/systemone",
            json={
                "model": "multilingual",
                "state": SPAM_MESSAGE,
                "questions": question_spec,
            },
        )

        assert response.status_code == 200
        body = response.json()
        probabilities = body["answers"]["category"]["probabilities"]
        assert set(probabilities) == set(spec_labels)
        assert all(isinstance(p, float) for p in probabilities.values())

    def test_choice_reports_the_multilingual_checkpoint(self, client, question_spec):
        response = client.post(
            "/v1/systemone",
            json={
                "model": "multilingual",
                "state": {"message": "Hey, how was your weekend?", "urls": []},
                "questions": question_spec,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["model"] == "laya-rl-agent-onnx"
        assert body["routing"]["model"] == "multilingual"

    def test_health_answers_ok(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
