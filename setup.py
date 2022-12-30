"""
FastAPI Tus implementation
-------------
Implements the tus.io server-side file-upload protocol
visit https://tus.io for more information
"""

from setuptools import setup, find_packages

setup(
    name='tuspy-fast-api',
    version='1.0.0',
    description='TUS py protocol implementation in FastAPI',
    author='Edi Hasaj',
    license='MIT',
    author_email='edihasaj@outlook.com',
    url='https://github.com/edihasaj/tuspy-fast-api',
    packages=find_packages(),
    platforms="any",
    include_package_data=True,
    py_modules=['tuspy-fast-api'],
    install_requires=[
        'fastapi>=0.88.0',
        'starlette>=0.22.0',
        'pydantic>=1.10.4',
    ],
    classifiers=[
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules'
    ]
)
