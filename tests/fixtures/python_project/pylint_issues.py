import os
import sys


def foo(x):
    unused_local = x
    return x + 1


class bad_class_name:
    def Method(self):
        pass
