from extractor.common.explain import system_prompt_for, SYSTEM_PROMPT
from extractor.common.names import container_of, ends_with_name, last_part, member_patterns


def test_cpp_names_split_on_double_colon_as_before():
    assert container_of("JSBSim::FGFDMExec::Run") == "JSBSim::FGFDMExec"
    assert last_part("JSBSim::FGFDMExec::Run") == "Run"
    assert container_of("main") is None
    assert ends_with_name("A::B::f", "B::f") and ends_with_name("f", "f") and not ends_with_name("A::Bf", "f")


def test_ada_names_split_on_dot():
    assert container_of("STM32.Device.Enable_Clock") == "STM32.Device"
    assert last_part("STM32.Device.Enable_Clock") == "Enable_Clock"
    assert ends_with_name("STM32.Device.Enable_Clock", "Device.Enable_Clock")
    assert not ends_with_name("STM32.Device.Enable_Clock", "able_Clock")
    assert member_patterns("STM32.Device") == ("STM32.Device::%", "STM32.Device.%")


def test_cpp_prompt_is_unchanged_and_ada_prompt_says_ada():
    assert system_prompt_for("cpp") is SYSTEM_PROMPT and system_prompt_for(None) is SYSTEM_PROMPT
    assert "legacy Ada codebase" in system_prompt_for("ada") and "C/C++" not in system_prompt_for("ada")
