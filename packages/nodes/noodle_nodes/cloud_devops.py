"""Cloud / DevOps nodes.

AWS SDK calls use boto3 (lazy-imported, workflow-env dep). SSH uses
paramiko. Git uses the system ``git`` binary via subprocess so users don't
need an extra Python package — they just need git installed in the env.

Credential metadata replaces inline secret fields with a single
"Credentials" picker per service.
"""

from __future__ import annotations

import asyncio
import json as json_mod
import shutil
import time
from typing import Any

from noodle.sdk import node
from noodle_nodes._creds import cred_multi


def _missing_driver(feature: str, package: str) -> RuntimeError:
    return RuntimeError(
        f"{feature} requires the Python package '{package}'. Install it in "
        "this workflow's environment and rebuild."
    )


def _aws_kwargs(region: str, creds: dict | None) -> dict[str, Any]:
    creds = creds or {}
    key = str(creds.get("aws_access_key_id") or "")
    secret = str(creds.get("aws_secret_access_key") or "")
    kwargs: dict[str, Any] = {"region_name": region or "us-east-1"}
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return kwargs


_AWS_CREDENTIALS_PARAM = {
    **cred_multi(
        "aws",
        "AWS credentials",
        ["aws_access_key_id", "aws_secret_access_key"],
    ),
    "description": (
        "Optional AWS access key + secret. Leave blank to use the default "
        "boto3 credential chain (env vars, instance role)."
    ),
}


# ============================================================================
# AWS Lambda
# ============================================================================


@node(
    name="AWS Lambda Invoke",
    id="aws_lambda_invoke",
    category="Integrations",
    icon="brand:awslambda",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "function_name": {
            "placeholder": "myFunction",
            "description": "Function name or ARN.",
        },
        "payload_json": {
            "description": (
                "Invocation payload as JSON. Falls back to the wired input "
                "(serialised to JSON)."
            ),
            "multiline": True,
        },
        "invocation_type": {
            "group": "Options",
            "choices": ["RequestResponse", "Event", "DryRun"],
            "description": "Synchronous, fire-and-forget, or validate-only.",
        },
        "credentials": _AWS_CREDENTIALS_PARAM,
    },
)
def aws_lambda_invoke(
    input: Any = None,
    region: str = "us-east-1",
    function_name: str = "",
    payload_json: str = "",
    invocation_type: str = "RequestResponse",
    credentials: dict | None = None,
) -> dict:
    """Invoke an AWS Lambda function."""
    if not function_name:
        raise ValueError("aws_lambda_invoke: function_name is required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("AWS Lambda", "boto3") from exc

    if payload_json:
        payload = payload_json.encode("utf-8")
    elif input is not None:
        payload = json_mod.dumps(input).encode("utf-8")
    else:
        payload = b"{}"

    client = boto3.client("lambda", **_aws_kwargs(region, credentials))
    response = client.invoke(
        FunctionName=function_name,
        InvocationType=invocation_type or "RequestResponse",
        Payload=payload,
    )
    body_bytes = response["Payload"].read() if "Payload" in response else b""
    try:
        body: Any = json_mod.loads(body_bytes.decode("utf-8")) if body_bytes else None
    except (ValueError, UnicodeDecodeError):
        body = body_bytes.decode("utf-8", "replace")
    return {
        "status_code": response.get("StatusCode"),
        "function_error": response.get("FunctionError"),
        "body": body,
    }


# ============================================================================
# AWS SQS
# ============================================================================


@node(
    name="AWS SQS Send Message",
    id="aws_sqs_send",
    category="Integrations",
    icon="brand:amazonsqs",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "queue_url": {
            "placeholder": "https://sqs.us-east-1.amazonaws.com/12345/queue",
            "description": "Full SQS queue URL.",
        },
        "body": {
            "description": (
                "Message body. Falls back to the wired input (dicts/lists "
                "are JSON-serialised)."
            ),
            "multiline": True,
        },
        "delay_seconds": {
            "group": "Options",
            "description": "Delay before the message is delivered (0-900).",
        },
        "credentials": _AWS_CREDENTIALS_PARAM,
    },
)
def aws_sqs_send(
    input: Any = None,
    region: str = "us-east-1",
    queue_url: str = "",
    body: str = "",
    delay_seconds: int = 0,
    credentials: dict | None = None,
) -> dict:
    """Send a single message to an SQS queue."""
    if not queue_url:
        raise ValueError("aws_sqs_send: queue_url is required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("AWS SQS", "boto3") from exc

    if body:
        message_body = body
    elif isinstance(input, dict | list):
        message_body = json_mod.dumps(input)
    elif input is not None:
        message_body = str(input)
    else:
        message_body = ""

    client = boto3.client("sqs", **_aws_kwargs(region, credentials))
    response = client.send_message(
        QueueUrl=queue_url,
        MessageBody=message_body,
        DelaySeconds=max(0, min(900, int(delay_seconds or 0))),
    )
    return {
        "message_id": response.get("MessageId"),
        "md5": response.get("MD5OfMessageBody"),
    }


@node(
    name="AWS SQS Receive Messages",
    id="aws_sqs_receive",
    category="Integrations",
    icon="brand:amazonsqs",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "queue_url": {
            "placeholder": "https://sqs.us-east-1.amazonaws.com/12345/queue",
            "description": "Full SQS queue URL.",
        },
        "max_messages": {
            "group": "Options",
            "description": "Up to 10 messages per call.",
        },
        "wait_time_seconds": {
            "group": "Options",
            "description": "Long-poll wait (0-20).",
        },
        "delete_after_receive": {
            "group": "Options",
            "description": "Auto-delete received messages from the queue.",
        },
        "credentials": _AWS_CREDENTIALS_PARAM,
    },
)
def aws_sqs_receive(
    input: Any = None,
    region: str = "us-east-1",
    queue_url: str = "",
    max_messages: int = 1,
    wait_time_seconds: int = 0,
    delete_after_receive: bool = False,
    credentials: dict | None = None,
) -> list:
    """Fetch up to N messages from an SQS queue."""
    _ = input
    if not queue_url:
        raise ValueError("aws_sqs_receive: queue_url is required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("AWS SQS", "boto3") from exc

    client = boto3.client("sqs", **_aws_kwargs(region, credentials))
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=max(1, min(10, int(max_messages or 1))),
        WaitTimeSeconds=max(0, min(20, int(wait_time_seconds or 0))),
    )
    messages = response.get("Messages", [])
    if delete_after_receive and messages:
        for msg in messages:
            client.delete_message(
                QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"]
            )
    return [
        {
            "message_id": msg.get("MessageId"),
            "body": msg.get("Body"),
            "receipt_handle": msg.get("ReceiptHandle"),
        }
        for msg in messages
    ]


# ============================================================================
# AWS SNS
# ============================================================================


@node(
    name="AWS SNS Publish",
    id="aws_sns_publish",
    category="Integrations",
    icon="brand:amazonsns",
    params={
        "region": {
            "placeholder": "us-east-1",
            "description": "AWS region.",
        },
        "topic_arn": {
            "placeholder": "arn:aws:sns:us-east-1:12345:my-topic",
            "description": "Topic ARN to publish to.",
        },
        "subject": {
            "group": "Options",
            "description": "Optional subject line (used by email subscribers).",
        },
        "message": {
            "description": (
                "Message body. Falls back to the wired input (dicts/lists "
                "are JSON-serialised)."
            ),
            "multiline": True,
        },
        "credentials": _AWS_CREDENTIALS_PARAM,
    },
)
def aws_sns_publish(
    input: Any = None,
    region: str = "us-east-1",
    topic_arn: str = "",
    subject: str = "",
    message: str = "",
    credentials: dict | None = None,
) -> dict:
    """Publish a message to an SNS topic."""
    if not topic_arn:
        raise ValueError("aws_sns_publish: topic_arn is required")
    try:
        import boto3
    except ImportError as exc:
        raise _missing_driver("AWS SNS", "boto3") from exc

    if message:
        body = message
    elif isinstance(input, dict | list):
        body = json_mod.dumps(input)
    elif input is not None:
        body = str(input)
    else:
        body = ""

    client = boto3.client("sns", **_aws_kwargs(region, credentials))
    kwargs = {"TopicArn": topic_arn, "Message": body}
    if subject:
        kwargs["Subject"] = subject
    response = client.publish(**kwargs)
    return {"message_id": response.get("MessageId")}


# ============================================================================
# SSH Execute
# ============================================================================


@node(
    name="SSH Execute",
    id="ssh_execute",
    category="System",
    icon="terminal",
    params={
        "host": {
            "placeholder": "1.2.3.4",
            "description": "Hostname or IP of the SSH server.",
        },
        "port": {"group": "Options", "description": "SSH port (default 22)."},
        "credentials": {
            **cred_multi(
                "ssh",
                "SSH credentials",
                ["username", "password", "private_key"],
            ),
            "description": (
                "SSH username plus either password or private_key (PEM). "
                "Both auth methods can be present; password takes precedence."
            ),
        },
        "command": {
            "description": "Command to run on the remote host.",
            "multiline": True,
        },
        "timeout_seconds": {
            "group": "Options",
            "description": "Connection + command timeout.",
        },
    },
)
def ssh_execute(
    input: Any = None,
    host: str = "",
    port: int = 22,
    credentials: dict | None = None,
    command: str = "",
    timeout_seconds: int = 30,
) -> dict:
    """Run a shell command on a remote host over SSH."""
    _ = input
    creds = credentials or {}
    username = str(creds.get("username") or "")
    password = str(creds.get("password") or "")
    private_key = str(creds.get("private_key") or "")
    if not host or not username or not command:
        raise ValueError(
            "ssh_execute: host, command, and credentials (username) are required"
        )
    if not password and not private_key:
        raise ValueError(
            "ssh_execute: credentials must include password or private_key"
        )
    try:
        import io as io_mod

        import paramiko
    except ImportError as exc:
        raise _missing_driver("SSH", "paramiko") from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = None
    if private_key:
        try:
            pkey = paramiko.RSAKey.from_private_key(io_mod.StringIO(private_key))
        except paramiko.SSHException:
            pkey = paramiko.Ed25519Key.from_private_key(io_mod.StringIO(private_key))
    try:
        client.connect(
            hostname=host,
            port=int(port or 22),
            username=username,
            password=password or None,
            pkey=pkey,
            timeout=max(1, int(timeout_seconds or 30)),
        )
        started = time.monotonic()
        stdin, stdout, stderr = client.exec_command(
            command, timeout=max(1, int(timeout_seconds or 30))
        )
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        returncode = stdout.channel.recv_exit_status()
        duration_ms = int((time.monotonic() - started) * 1000)
    finally:
        client.close()
    return {
        "stdout": out,
        "stderr": err,
        "returncode": returncode,
        "duration_ms": duration_ms,
    }


# ============================================================================
# Git
# ============================================================================


@node(
    name="Git Clone",
    id="git_clone",
    category="System",
    icon="brand:git",
    params={
        "url": {
            "placeholder": "https://github.com/user/repo.git",
            "description": "Repository URL.",
        },
        "directory": {
            "placeholder": "/tmp/repo",
            "description": "Target directory (will be created).",
        },
        "branch": {
            "group": "Options",
            "placeholder": "main",
            "description": "Optional branch to clone.",
        },
        "depth": {
            "group": "Options",
            "description": "Shallow clone depth (0 = full history).",
        },
    },
)
async def git_clone(
    input: Any = None,
    url: str = "",
    directory: str = "",
    branch: str = "",
    depth: int = 0,
) -> dict:
    """Clone a git repository using the system git binary."""
    _ = input
    if not url or not directory:
        raise ValueError("git_clone: url and directory are required")
    if shutil.which("git") is None:
        raise RuntimeError(
            "git_clone: 'git' binary not found on PATH; install git in this env"
        )
    argv = ["git", "clone"]
    if branch:
        argv.extend(["--branch", branch])
    if depth and depth > 0:
        argv.extend(["--depth", str(int(depth))])
    argv.extend([url, directory])
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    duration_ms = int((time.monotonic() - started) * 1000)
    returncode = proc.returncode or 0
    if returncode != 0:
        raise RuntimeError(
            f"git_clone: exit {returncode}: "
            f"{stderr_bytes.decode('utf-8', 'replace')[:500]}"
        )
    return {
        "directory": directory,
        "stdout": stdout_bytes.decode("utf-8", "replace"),
        "stderr": stderr_bytes.decode("utf-8", "replace"),
        "duration_ms": duration_ms,
    }


@node(
    name="Git Pull",
    id="git_pull",
    category="System",
    icon="brand:git",
    params={
        "directory": {
            "placeholder": "/path/to/repo",
            "description": "Existing repo directory to pull into.",
        },
        "remote": {
            "group": "Options",
            "placeholder": "origin",
            "description": "Optional remote name.",
        },
        "branch": {
            "group": "Options",
            "description": "Optional branch to pull.",
        },
    },
)
async def git_pull(
    input: Any = None,
    directory: str = "",
    remote: str = "",
    branch: str = "",
) -> dict:
    """Run ``git pull`` in an existing repo directory."""
    _ = input
    if not directory:
        raise ValueError("git_pull: directory is required")
    if shutil.which("git") is None:
        raise RuntimeError(
            "git_pull: 'git' binary not found on PATH; install git in this env"
        )
    argv = ["git", "-C", directory, "pull"]
    if remote:
        argv.append(remote)
    if branch:
        argv.append(branch)
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    duration_ms = int((time.monotonic() - started) * 1000)
    returncode = proc.returncode or 0
    if returncode != 0:
        raise RuntimeError(
            f"git_pull: exit {returncode}: "
            f"{stderr_bytes.decode('utf-8', 'replace')[:500]}"
        )
    return {
        "stdout": stdout_bytes.decode("utf-8", "replace"),
        "stderr": stderr_bytes.decode("utf-8", "replace"),
        "duration_ms": duration_ms,
    }
