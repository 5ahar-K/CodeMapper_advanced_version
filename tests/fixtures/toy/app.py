import os
from . import util as u
from .util import helper, Base


class Worker(Base):
    def __init__(self):
        super().__init__()
        self.n = 0

    def run(self):
        self.step()                      # own method
        self.load()                      # inherited from Base
        helper()                         # explicit import
        u.helper()                       # module attribute
        os.path.join("a", "b")           # external
        ", ".join(["a"])                 # literal receiver
        x = Base()                       # constructor
        x.save()                         # type inferred from x = Base()

    def step(self):
        pass

    def save(self):                      # same name as Base.save
        pass


def main():
    w = Worker()
    w.run()
    thing.save()                         # unknown receiver, two `save` methods -> ambiguous
    len([1])                             # builtin
    callback = None
    callback()                           # local variable


def with_nested():
    def inner():
        return 1
    return inner()
