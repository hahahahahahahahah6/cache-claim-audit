import functools

@functools.lru_cache(maxsize=128)
def work(value):
    return value * 2
