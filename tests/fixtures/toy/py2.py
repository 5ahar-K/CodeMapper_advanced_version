def old_style(name, async=False):
    try:
        x = 1
    except ValueError, e:
        print "bad"
    raise ValueError, "boom"


def caller():
    old_style("x")
