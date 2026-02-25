from setuptools import setup, find_packages

setup(
    name="clawconductor",
    version="0.1.0",
    description="Lightweight escalation middleware between OpenClaw and LiteLLM",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pyyaml>=6.0",
        "fastapi>=0.110.0",
        "uvicorn[standard]>=0.29.0",
        "httpx>=0.27.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0"],
    },
)
