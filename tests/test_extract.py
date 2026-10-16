"""Hand-worked self-test for the god-math extractor. Every value is derived by hand.

Two source files, one language (Java here, but the extractor is lexical and language-
neutral). handle is the sun: it builds and radiates domain objects over calls to our own
code; validate reads; save pushes to a library (not our code) so does not count.

  * Ctl.handle(o, id): calls validate(o)=1 arg, enrich(o, p)=2, save(o)=1 (save is ours),
    tag(id)=1, getName()=0. All callees are our functions. out_gen = 1+2+1+1 = 5. Nothing
    calls handle, so in_gen = 0, gain = 5.
  * Ctl.validate(o): calls getName() (0 args). out_gen = 0; handle passes it 1; gain = 0.
  * Ctl.enrich(o, p): calls addPet(p) (1). out_gen = 1; handle passes 2; gain = 0.
  * Ctl.tag(id): no call. out_gen = 0; handle passes 1; gain = 0.
  * Repo.save(o): calls jdbc.update("insert", o) -> update is NOT one of our functions, so
    it is library code and does not count. out_gen = 0; handle passes 1; gain = 0.

So gain is {handle: 5, all others: 0}; handle is the only generator and the sole outlier.
WMC per file is the sum of function CC.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coupling_divergence import extract

SRC = {
    "Owner.java": """package t;
public class Owner {
    public String getName() { return name; }
    public void addPet(Pet p) { String s = p.toString(); }
}
""",
    "Repo.java": """package t;
public class Repo {
    public void save(Owner o) { jdbc.update("insert", o); }
}
""",
    "Ctl.java": """package t;
public class Ctl {
    public String handle(Owner o, int id) {
        Pet p = new Pet();
        validate(o);
        enrich(o, p);
        save(o);
        int t = tag(id);
        return getName();
    }
    void validate(Owner o) { if (getName() == null) { return; } }
    void enrich(Owner o, Pet p) { addPet(p); }
    int tag(int id) { return id * 2; }
}
""",
}
EXPECTED_GAIN = {"handle": 5, "validate": 0, "enrich": 0, "save": 0, "tag": 0}


def _tree():
    root = tempfile.mkdtemp(prefix="coupling_divergence_test_")
    for name, body in SRC.items():
        with open(os.path.join(root, name), "w") as fh:
            fh.write(body)
    return root


def test_generation_gain():
    res = extract.analyze_tree(_tree())
    by_name = {fid[1]: r["gain"] for fid, r in res["funcs"].items()}
    for name, want in EXPECTED_GAIN.items():
        assert by_name.get(name) == want, f"{name}: {by_name.get(name)} != {want}"


def test_outlier_flags_the_extreme():
    # The outlier test needs a distribution; a lone generator is correctly not an outlier
    # (you cannot call a single value an outlier). Build a flat field with one spike.
    funcs = {("f.java", f"m{i}"): {"gain": 1, "cc": 1, "out_gen": 1, "in_gen": 0}
             for i in range(12)}
    funcs[("f.java", "god")] = {"gain": 20, "cc": 1, "out_gen": 20, "in_gen": 0}
    outs = extract.outliers(funcs)
    assert {fid[1] for fid in outs} == {"god"}, outs


def test_lone_generator_is_not_an_outlier():
    funcs = {("f.java", "only"): {"gain": 9, "cc": 1, "out_gen": 9, "in_gen": 0}}
    assert extract.outliers(funcs) == set()


def test_wmc_is_per_file_cc_sum():
    res = extract.analyze_tree(_tree())
    # Ctl.java: handle CC 1 (no branches counted beyond entry) ... assert it is the heaviest
    wmc = res["wmc"]
    assert max(wmc, key=lambda f: wmc[f]) == "Ctl.java", wmc


if __name__ == "__main__":
    ok = True
    for fn in (test_generation_gain, test_outlier_flags_the_extreme,
               test_lone_generator_is_not_an_outlier, test_wmc_is_per_file_cc_sum):
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            ok = False
            print(f"  [FAIL] {fn.__name__}: {e}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
