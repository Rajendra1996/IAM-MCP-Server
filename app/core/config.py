"""Central configuration for local services and container deployments."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://iam_user:iam_password@127.0.0.1:5432/iam"
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")
    iam_api_url: str = os.getenv("IAM_API_URL", "http://127.0.0.1:9000")
    hr_api_url: str = os.getenv("HR_API_URL", "http://127.0.0.1:9100")
    itsm_api_url: str = os.getenv("ITSM_API_URL", "http://127.0.0.1:9201")
    iam_mcp_host: str = os.getenv("IAM_MCP_HOST", "127.0.0.1")
    iam_mcp_port: int = int(os.getenv("IAM_MCP_PORT", "8001"))
    hr_mcp_host: str = os.getenv("HR_MCP_HOST", "127.0.0.1")
    hr_mcp_port: int = int(os.getenv("HR_MCP_PORT", "8002"))
    itsm_mcp_host: str = os.getenv("ITSM_MCP_HOST", "127.0.0.1")
    itsm_mcp_port: int = int(os.getenv("ITSM_MCP_PORT", "8003"))
    gateway_host: str = os.getenv("GATEWAY_HOST", "127.0.0.1")
    gateway_port: int = int(os.getenv("GATEWAY_PORT", "8080"))
    iam_backend_url: str = os.getenv("IAM_BACKEND_URL", "http://127.0.0.1:8081")
    hr_backend_url: str = os.getenv("HR_BACKEND_URL", "http://127.0.0.1:8002")
    itsm_backend_url: str = os.getenv("ITSM_BACKEND_URL", "http://127.0.0.1:8003")
    iam_replica_a_url: str = os.getenv("IAM_REPLICA_A_URL", "http://127.0.0.1:8011")
    iam_replica_b_url: str = os.getenv("IAM_REPLICA_B_URL", "http://127.0.0.1:8012")
    instance_id: str = os.getenv("INSTANCE_ID", "iam-local")
    iam_jwt_secret: str = os.getenv("IAM_JWT_SECRET", "dev-only-secret-change-me")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-6-luna")
    rate_limit: int = int(os.getenv("RATE_LIMIT", "20"))
    rate_window_seconds: int = int(os.getenv("RATE_WINDOW_SECONDS", "60"))

    @property
    def iam_mcp_url(self) -> str:
        return os.getenv("IAM_MCP_URL", f"http://127.0.0.1:{self.iam_mcp_port}/mcp")

    @property
    def hr_mcp_url(self) -> str:
        return os.getenv("HR_MCP_URL", f"http://127.0.0.1:{self.hr_mcp_port}/mcp")

    @property
    def itsm_mcp_url(self) -> str:
        return os.getenv("ITSM_MCP_URL", f"http://127.0.0.1:{self.itsm_mcp_port}/mcp")

    @property
    def gateway_url(self) -> str:
        return os.getenv("GATEWAY_URL", f"http://127.0.0.1:{self.gateway_port}")


settings = Settings()
