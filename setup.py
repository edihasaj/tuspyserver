"""
FastAPI Tus implementation
-------------
Implements the tus.io server-side file-upload protocol
visit https://tus.io for more information
"""

from setuptools import find_packages, setup

with open("README.md", "r") as f:
    long_description = f.read()

setup(
    name="tuspyserver",
    version="2.2.0",
    description="TUS py protocol implementation in FastAPI",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Edi Hasaj",
    license="MIT",
    author_email="edi.hasaj@applifyer.com",
    url="https://github.com/edihasaj/tuspy-fast-api",
    packages=find_packages(),
    platforms="any",
    include_package_data=True,
    install_requires=[
        "fastapi>=0.110.0",
        "pydantic>=2.6.2",
    ],
    classifiers=[
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
