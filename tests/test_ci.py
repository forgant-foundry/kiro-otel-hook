from kiro_mlflow_hook.ci import ci_attributes


def test_no_attributes_outside_gitlab_ci():
    assert ci_attributes({"CI_PIPELINE_ID": "42"}) == {}


def test_maps_only_set_gitlab_variables():
    attrs = ci_attributes(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "42",
            "CI_JOB_ID": "7",
            "CI_COMMIT_SHA": "deadbeef",
            "CI_MERGE_REQUEST_IID": "",
            "GITLAB_USER_LOGIN": "dev",
            "UNRELATED": "x",
        }
    )
    assert attrs == {
        "gitlab.pipeline.id": "42",
        "gitlab.job.id": "7",
        "gitlab.commit.sha": "deadbeef",
        "gitlab.user.login": "dev",
    }
