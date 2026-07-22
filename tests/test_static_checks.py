from verifier.static_checks import check_submission, lean_tokens

from conftest import manifest


def test_comments_and_strings_do_not_trigger_commands():
    text = '-- import Evil\ndef note : String := "sorry import"\ntheorem target : True := by trivial\n'
    assert check_submission(text, manifest()).valid


def test_prohibited_commands_and_sorry_are_rejected():
    result = check_submission("import Mathlib\ntheorem target : True := by sorry\n", manifest())
    assert not result.valid
    assert any("import" in item for item in result.violations)
    assert any("sorry" in item for item in result.violations)


def test_hash_commands_and_run_tac_are_rejected():
    assert not check_submission("#eval 1\n", manifest()).valid
    assert not check_submission("theorem target : True := by run_tac pure ()\n", manifest()).valid


def test_native_decide_is_rejected():
    result = check_submission("theorem target : True := by native_decide\n", manifest())
    assert not result.valid
    assert any("native_decide is prohibited" in item for item in result.violations)


def test_initializer_attributes_and_meta_declarations_are_rejected():
    initializer = (
        "@[init] def payload : IO Unit := IO.println \"executed\"\n"
        "theorem target : True := by trivial\n"
    )
    assert not check_submission(initializer, manifest()).valid
    assert not check_submission("@[simp] theorem target : True := by trivial\n", manifest()).valid
    assert not check_submission("meta def payload : IO Unit := pure ()\n", manifest()).valid


def test_interpolated_string_cannot_hide_executable_syntax():
    payload = 'def hidden : String := s!"{by run_tac pure (); exact ""}"\n'
    result = check_submission(payload, manifest())
    assert not result.valid
    assert any("interpolated strings are prohibited" in violation for violation in result.violations)


def test_prohibited_commands_do_not_need_to_start_a_line():
    result = check_submission("namespace Hidden axiom forged : False end Hidden\n", manifest())
    assert not result.valid
    assert any("axiom is prohibited" in violation for violation in result.violations)
    assert not check_submission("namespace Hidden #eval 1 end Hidden\n", manifest()).valid
    assert not check_submission("namespace Hidden constant forged : False end Hidden\n", manifest()).valid
    assert not check_submission("run_cmd IO.println \"executed\"\n", manifest()).valid
    assert not check_submission("init_quot\n", manifest()).valid
    assert not check_submission("local notation \"cheat\" => True\n", manifest()).valid


def test_qualified_and_quoted_sorry_axiom_is_rejected():
    result = check_submission(
        "theorem target : True := by exact _root_.«sorryAx» True true\n",
        manifest(),
    )
    assert not result.valid
    assert any("sorryAx is prohibited" in violation for violation in result.violations)

    multiple = check_submission("theorem target : True := by exact sorry.axiom\n", manifest())
    assert multiple.violations[0].startswith("sorry is prohibited")


def test_nested_comments_are_tokenized_safely():
    tokens = lean_tokens("/- outer /- sorry -/ import -/ theorem target : True := by trivial")
    assert all(token.value not in {"sorry", "import"} for token in tokens)


def test_nat_answer_must_be_closed_literal():
    policy = {"definition_name": "Bounty.submittedAnswer", "syntax": "nat_literal"}
    valid = "def helper : ℕ := 2\ndef submittedAnswer : ℕ := 4\ntheorem target : True := by trivial\n"
    invalid = "def submittedAnswer : ℕ := 2 + 2\ntheorem target : True := by trivial\n"
    assert check_submission(valid, manifest(answer_policy=policy)).valid
    assert not check_submission(invalid, manifest(answer_policy=policy)).valid


def test_numeric_literal_cannot_use_a_malicious_ofnat_instance():
    policy = {"definition_name": "Bounty.submittedAnswer", "syntax": "nat_literal"}
    payload = (
        "local instance : OfNat Nat 4 where ofNat := 999\n"
        "def submittedAnswer : ℕ := 4\n"
        "theorem target : True := by trivial\n"
    )
    result = check_submission(payload, manifest(answer_policy=policy))
    assert not result.valid
    assert any("instance is prohibited" in violation for violation in result.violations)


def test_answer_literal_cannot_be_satisfied_by_namespace_decoy():
    policy = {"definition_name": "Bounty.submittedAnswer", "syntax": "nat_literal"}
    payload = (
        "namespace Decoy\n"
        "def submittedAnswer : ℕ := 4\n"
        "end Decoy\n"
        "def submittedAnswer : ℕ := 2 + 2\n"
        "theorem target : True := by trivial\n"
    )
    result = check_submission(payload, manifest(answer_policy=policy))
    assert not result.valid
    assert any("exactly one regular def submittedAnswer" in violation for violation in result.violations)

    abbrev_payload = (
        "namespace Decoy\n"
        "def submittedAnswer : ℕ := 4\n"
        "end Decoy\n"
        "abbrev submittedAnswer : ℕ := 2 + 2\n"
        "theorem target : True := by trivial\n"
    )
    assert not check_submission(abbrev_payload, manifest(answer_policy=policy)).valid

    qualified_payload = (
        "namespace Decoy\n"
        "def submittedAnswer : ℕ := 4\n"
        "end Decoy\n"
        "end Bounty\n"
        "def Bounty.submittedAnswer : ℕ := 2 + 2\n"
        "namespace Bounty\n"
        "theorem target : True := by trivial\n"
    )
    assert not check_submission(qualified_payload, manifest(answer_policy=policy)).valid


def test_invisible_unicode_is_rejected():
    payload = "theorem target : True := by\u202e trivial\n"
    result = check_submission(payload, manifest())
    assert not result.valid
    assert any("Unicode" in violation for violation in result.violations)


def test_source_declaration_dependency_is_rejected():
    result = check_submission(
        "theorem target : True := by exact VerifierFixtures.direct\n",
        manifest(forbidden=("VerifierFixtures.direct",)),
    )
    assert not result.valid
    quoted = check_submission(
        "theorem target : True := by exact _root_.VerifierFixtures.«direct»\n",
        manifest(forbidden=("VerifierFixtures.direct",)),
    )
    assert not quoted.valid


def test_rooted_source_dependency_with_dots_inside_quoted_names_is_rejected():
    source = "Kourovka.«19.25».kourovka.«19.25»"
    rooted = check_submission(
        f"theorem target : True := by exact _root_.{source}\n",
        manifest(forbidden=(source,)),
    )
    short = check_submission(
        "theorem target : True := by exact «19.25»\n",
        manifest(forbidden=(source,)),
    )
    assert not rooted.valid
    assert not short.valid
