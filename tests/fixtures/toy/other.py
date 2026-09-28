class A:
    def save(self):
        pass

    def go(self):
        self.save()      # must resolve to A.save ONLY (the old heuristic also linked B.save)


class B:
    def save(self):
        pass
