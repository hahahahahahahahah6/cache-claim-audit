from functools import lru_cache

@lru_cache
def work(value):
    return value * 2
