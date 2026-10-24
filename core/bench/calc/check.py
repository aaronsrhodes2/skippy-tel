"""Grades a calc answer: python check.py answer.py -> prints passed/total, exits 0 only when all pass."""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("answer", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GOOD = ["1+2*3", "(1+2)*3", "2**3**2", "-2**2", "2**-1", "-(3)", "--3", "+-+4", "10/4", "7%3", "-7%3", "2*-3",
        "1 - 2 - 3", "2 ** 3 * 2", ".5+2.", "((((1))))", "  3   *(  4 +5 )", "100/10/5", "2*3**2", "-3**-2",
        "1.5*4", "8-(-2)", "(2+3)**2%7", "0.1+0.2", "6/-(1+2)"]
BAD = ["", "   ", "(1+2", "1+2)", "1+", "*2", "1 2", "2**", "3 $ 4", "()", "1+*2", "abc"]
ZERO = ["1/0", "5%0", "1/(2-2)"]

ok = total = 0
for e in GOOD:
    total += 1
    try:
        got = mod.evaluate(e)
        if abs(got - eval(e)) < 1e-9:
            ok += 1
        else:
            print(f"WRONG {e!r}: {got} != {eval(e)}")
    except Exception as x:
        print(f"RAISED {e!r}: {x!r}")
for e in BAD:
    total += 1
    try:
        print(f"NO ERROR {e!r}: {mod.evaluate(e)}")
    except ValueError:
        ok += 1
    except Exception as x:
        print(f"WRONG ERROR {e!r}: {x!r}")
for e in ZERO:
    total += 1
    try:
        print(f"NO ERROR {e!r}: {mod.evaluate(e)}")
    except ZeroDivisionError:
        ok += 1
    except Exception as x:
        print(f"WRONG ERROR {e!r}: {x!r}")
print(f"{ok}/{total}")
sys.exit(0 if ok == total else 1)
