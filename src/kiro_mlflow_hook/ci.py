"""CI correlation attributes: *where* an agent run happened.

When the hook runs inside a GitLab CI job (GITLAB_CI is set), the standard
GitLab predefined variables are mapped onto `gitlab.*` attributes so every
trace, metric, and log record can be pivoted by pipeline, job, commit, merge
request, and user. Outside CI this returns nothing.

These are identifiers and URLs, not agent content, so they are attached at
every KIRO_OTEL_GENAI_DETAIL level -- "who ran what, where" is the point of
an audit trail. They go on the OTel Resource (shared by the tracer, meter,
and logger providers) so metrics and logs carry them too, not just spans.
"""

from __future__ import annotations

from collections.abc import Mapping

# GitLab predefined variable -> attribute key. Only variables that are
# actually set (and non-empty) are emitted.
GITLAB_ATTRIBUTE_MAP: dict[str, str] = {
    "CI_SERVER_URL": "gitlab.server.url",
    "CI_PROJECT_ID": "gitlab.project.id",
    "CI_PROJECT_PATH": "gitlab.project.path",
    "CI_PROJECT_URL": "gitlab.project.url",
    "CI_PIPELINE_ID": "gitlab.pipeline.id",
    "CI_PIPELINE_IID": "gitlab.pipeline.iid",
    "CI_PIPELINE_URL": "gitlab.pipeline.url",
    "CI_PIPELINE_SOURCE": "gitlab.pipeline.source",
    "CI_JOB_ID": "gitlab.job.id",
    "CI_JOB_NAME": "gitlab.job.name",
    "CI_JOB_STAGE": "gitlab.job.stage",
    "CI_JOB_URL": "gitlab.job.url",
    "CI_COMMIT_SHA": "gitlab.commit.sha",
    "CI_COMMIT_REF_NAME": "gitlab.commit.ref_name",
    "CI_COMMIT_BRANCH": "gitlab.commit.branch",
    "CI_COMMIT_TAG": "gitlab.commit.tag",
    "CI_MERGE_REQUEST_IID": "gitlab.merge_request.iid",
    "CI_MERGE_REQUEST_PROJECT_PATH": "gitlab.merge_request.project_path",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "gitlab.merge_request.source_branch",
    "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "gitlab.merge_request.target_branch",
    "GITLAB_USER_ID": "gitlab.user.id",
    "GITLAB_USER_LOGIN": "gitlab.user.login",
    "CI_RUNNER_ID": "gitlab.runner.id",
    "CI_RUNNER_DESCRIPTION": "gitlab.runner.description",
}


def ci_attributes(environ: Mapping[str, str]) -> dict[str, str]:
    if not environ.get("GITLAB_CI"):
        return {}
    return {attr: environ[var] for var, attr in GITLAB_ATTRIBUTE_MAP.items() if environ.get(var)}
