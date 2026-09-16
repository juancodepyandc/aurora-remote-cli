from setuptools import setup, find_packages

setup(
    name="aurora-cli",
    version="1.0.0",
    description="Aurora AI Agent — Remote CLI Client",
    packages=find_packages(include=["aurora_cli*"]),
    install_requires=[
        "httpx>=0.27",
        "rich>=13.0",
        "click>=8.0",
        "prompt-toolkit>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "aurora=aurora_cli.cli:main"
        ]
    },
)
