"""
Tests for Phase 5: Fine-Tuning - admin settings, training data export, and run management.
"""
import json

from tests.conftest import auth_header


class TestFineTuneTables:
    def test_admin_settings_table(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='admin_settings'"
        ).fetchall()
        assert len(rows) == 1

    def test_fine_tune_runs_table(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fine_tune_runs'"
        ).fetchall()
        assert len(rows) == 1

    def test_fine_tune_training_data_table(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fine_tune_training_data'"
        ).fetchall()
        assert len(rows) == 1


class TestAdminSettings:
    def test_get_empty(self, client, admin_token):
        r = client.get("/api/admin/settings", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == {}

    def test_set_and_get(self, client, admin_token):
        r = client.put("/api/admin/settings", json={
            "fine_tuning_enabled": "true",
            "max_training_records": "1000",
        }, headers=auth_header(admin_token))
        assert r.status_code == 200

        r2 = client.get("/api/admin/settings", headers=auth_header(admin_token))
        settings = r2.get_json()
        assert settings["fine_tuning_enabled"]["value"] == "true"
        assert settings["max_training_records"]["value"] == "1000"

    def test_update_existing(self, client, admin_token):
        client.put("/api/admin/settings", json={"fine_tuning_enabled": "true"}, headers=auth_header(admin_token))
        client.put("/api/admin/settings", json={"fine_tuning_enabled": "false"}, headers=auth_header(admin_token))

        r = client.get("/api/admin/settings", headers=auth_header(admin_token))
        assert r.get_json()["fine_tuning_enabled"]["value"] == "false"

    def test_non_admin(self, client, user_token):
        assert client.get("/api/admin/settings", headers=auth_header(user_token)).status_code == 403
        assert client.put("/api/admin/settings", json={"x": "y"}, headers=auth_header(user_token)).status_code == 403

    def test_unauthenticated(self, client):
        assert client.get("/api/admin/settings").status_code == 401


class TestFeatureFlag:
    def test_export_blocked_when_disabled(self, client, admin_token):
        """Fine-tune export should fail when feature is disabled."""
        r = client.post("/api/fine-tune/export", json={}, headers=auth_header(admin_token))
        assert r.status_code == 403
        assert "not enabled" in r.get_json()["error"]

    def test_create_run_blocked_when_disabled(self, client, admin_token):
        r = client.post("/api/fine-tune/runs", json={"base_model": "qwen2.5:7b"}, headers=auth_header(admin_token))
        assert r.status_code == 403

    def test_export_allowed_when_enabled(self, client, admin_token):
        # Enable feature
        client.put("/api/admin/settings", json={"fine_tuning_enabled": "true"}, headers=auth_header(admin_token))

        r = client.post("/api/fine-tune/export", json={}, headers=auth_header(admin_token))
        assert r.status_code == 200
        data = r.get_json()
        assert "records" in data
        assert data["count"] == 0  # No summaries to export


class TestFineTuneRuns:
    def _enable_ft(self, client, admin_token):
        client.put("/api/admin/settings", json={"fine_tuning_enabled": "true"}, headers=auth_header(admin_token))

    def test_list_empty(self, client, admin_token):
        r = client.get("/api/fine-tune/runs", headers=auth_header(admin_token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_create_run(self, client, admin_token):
        self._enable_ft(client, admin_token)

        r = client.post("/api/fine-tune/runs", json={
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "adapter_name": "lessonlens-qwen7b",
        }, headers=auth_header(admin_token))
        assert r.status_code == 201
        run_id = r.get_json()["id"]

        # Verify in list
        runs = client.get("/api/fine-tune/runs", headers=auth_header(admin_token)).get_json()
        assert len(runs) == 1
        assert runs[0]["base_model"] == "Qwen/Qwen2.5-7B-Instruct"
        assert runs[0]["adapter_name"] == "lessonlens-qwen7b"
        assert runs[0]["status"] == "pending"

    def test_create_run_missing_model(self, client, admin_token):
        self._enable_ft(client, admin_token)
        r = client.post("/api/fine-tune/runs", json={}, headers=auth_header(admin_token))
        assert r.status_code == 400

    def test_update_run_status(self, client, admin_token):
        self._enable_ft(client, admin_token)

        r = client.post("/api/fine-tune/runs", json={"base_model": "test-model"}, headers=auth_header(admin_token))
        run_id = r.get_json()["id"]

        # Update to running
        r2 = client.put(f"/api/fine-tune/runs/{run_id}", json={
            "status": "running",
            "training_records": 50,
        }, headers=auth_header(admin_token))
        assert r2.status_code == 200

        # Update to completed with metrics
        r3 = client.put(f"/api/fine-tune/runs/{run_id}", json={
            "status": "completed",
            "metrics": {"train_loss": 0.15, "epochs": 3},
            "output_path": "/path/to/adapter",
        }, headers=auth_header(admin_token))
        assert r3.status_code == 200

        # Verify
        runs = client.get("/api/fine-tune/runs", headers=auth_header(admin_token)).get_json()
        run = runs[0]
        assert run["status"] == "completed"
        assert run["metrics"]["train_loss"] == 0.15
        assert run["output_path"] == "/path/to/adapter"
        assert run["completed_at"] is not None

    def test_update_run_not_found(self, client, admin_token):
        r = client.put("/api/fine-tune/runs/9999", json={"status": "failed"}, headers=auth_header(admin_token))
        assert r.status_code == 404

    def test_non_admin(self, client, user_token):
        assert client.get("/api/fine-tune/runs", headers=auth_header(user_token)).status_code == 403
        assert client.post("/api/fine-tune/runs", json={}, headers=auth_header(user_token)).status_code == 403


class TestIsFeatureEnabled:
    def test_default_false(self, db):
        from app import _is_feature_enabled
        assert _is_feature_enabled(db, "nonexistent_feature") is False

    def test_default_true(self, db):
        from app import _is_feature_enabled
        assert _is_feature_enabled(db, "nonexistent_feature", default=True) is True

    def test_enabled_values(self, db):
        from app import _is_feature_enabled
        for val in ["true", "1", "yes", "enabled"]:
            db.execute("INSERT OR REPLACE INTO admin_settings (key, value) VALUES ('test_flag', ?)", (val,))
            db.commit()
            assert _is_feature_enabled(db, "test_flag") is True

    def test_disabled_values(self, db):
        from app import _is_feature_enabled
        for val in ["false", "0", "no", "disabled"]:
            db.execute("INSERT OR REPLACE INTO admin_settings (key, value) VALUES ('test_flag', ?)", (val,))
            db.commit()
            assert _is_feature_enabled(db, "test_flag") is False
