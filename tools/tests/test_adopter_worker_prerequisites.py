import pathlib
import re


REPO = pathlib.Path(__file__).parents[2]
README = REPO / "README.md"
JOURNEY = REPO / "tools" / "uat_adopter_journey.sh"
GENERATOR_COMMANDS = [
    "python3 tools/build_site.py --check",
    "python3 tools/build_instructor_bundle.py",
    "node app/worker/scripts/bundle-editor-data.mjs",
]


def _readme_sequences():
    text = README.read_text(encoding="utf-8")
    blocks = re.findall(
        r"<!-- adopter-worker-generators -->\s*```bash\n(.*?)```",
        text,
        flags=re.DOTALL,
    )
    return [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in blocks
    ]


def _journey_sequence(text):
    match = re.search(
        r"# adopter-worker-generators:start\n(.*?)"
        r"\n\s*# adopter-worker-generators:end",
        text,
        flags=re.DOTALL,
    )
    assert match, "adopter Worker generator sequence is not marked in the journey script"
    commands = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line:
            assert line.startswith("run_clone_command ")
            commands.append(line.removeprefix("run_clone_command "))
    return commands


def _function_body(text, name):
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", text, flags=re.DOTALL | re.MULTILINE)
    assert match, f"missing shell function: {name}"
    return match.group(1)


def test_readme_and_adopter_journey_use_the_same_worker_generator_sequence():
    assert _readme_sequences() == [GENERATOR_COMMANDS, GENERATOR_COMMANDS]

    journey = JOURNEY.read_text(encoding="utf-8")
    assert _journey_sequence(journey) == GENERATOR_COMMANDS

    for function, final_command in [
        ("run_worker_tests", "node --test test/*.test.js"),
        ("run_worker_dry_run", "npx wrangler@4 deploy --dry-run"),
    ]:
        body = _function_body(journey, function)
        assert body.index("prepare_worker_data") < body.index(final_command)
