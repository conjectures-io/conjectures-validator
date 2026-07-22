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


def test_prohibited_commands_do_not_need_to_start_a_line():
    result = check_submission("namespace Hidden axiom forged : False end Hidden\n", manifest())
    assert not result.valid
    assert any("axiom is prohibited" in violation for violation in result.violations)
    assert not check_submission("namespace Hidden #eval 1 end Hidden\n", manifest()).valid
    assert not check_submission("namespace Hidden constant forged : False end Hidden\n", manifest()).valid
    assert not check_submission("run_cmd IO.println \"executed\"\n", manifest()).valid


def test_qualified_and_quoted_sorry_axiom_is_rejected():
    result = check_submission(
        "theorem target : True := by exact _root_.«sorryAx» True true\n",
        manifest(),
    )
    assert not result.valid
    assert any("sorryAx is prohibited" in violation for violation in result.violations)


def test_nested_comments_are_tokenized_safely():
    tokens = lean_tokens("/- outer /- sorry -/ import -/ theorem target : True := by trivial")
    assert all(token.value not in {"sorry", "import"} for token in tokens)


def test_nat_answer_must_be_closed_literal():
    policy = {"definition_name": "Bounty.submittedAnswer", "syntax": "nat_literal"}
    valid = "def helper : ℕ := 2\ndef submittedAnswer : ℕ := 4\ntheorem target : True := by trivial\n"
    invalid = "def submittedAnswer : ℕ := 2 + 2\ntheorem target : True := by trivial\n"
    assert check_submission(valid, manifest(answer_policy=policy)).valid
    assert not check_submission(invalid, manifest(answer_policy=policy)).valid


def test_source_declaration_dependency_is_rejected():
    result = check_submission(
        "theorem target : True := by exact VerifierFixtures.direct\n",
        manifest(forbidden=("VerifierFixtures.direct",)),
    )
    assert not result.valid
