"""Deliberately insecure code to trigger Bandit findings."""

import subprocess

DB_PASSWORD = "hunter2"  # hardcoded password


def run_query(user_input):
    query = "SELECT * FROM users WHERE name = '" + user_input + "'"  # SQL injection vector
    return query


def run_shell(cmd):
    subprocess.call(cmd, shell=True)  # shell=True with a variable command


def evaluate(expression):
    return eval(expression)  # eval on untrusted input
