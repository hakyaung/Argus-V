# 파일 위치: ~/Desktop/Argus-V/argus_hook/agent_loop.py
# 파일 명: agent_loop.py

import argparse
import json
import os
import subprocess
import sys
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# Argus-V 공식 모듈 임포트
# ============================================================
from argus_v.agent_actions import (
    generate_sandbox_action,
    observation_message,
    fakenet_pages,
    parse_action,
    system_prompt_for,
)

from argus_v.activations import (
    ActionSpan,
    BlockAdapter,
    HookInspection,
    ModelIdentity,
    SnapshotContext,
    extract_prefix,
    write_capture,
)


class RealSandboxClient:
    """sandbox.py API를 직접 호출하여 안전하게 코드를 실행하는 클라이언트"""
    def __init__(self, sandbox_script="~/Desktop/Argus-V/SANDBOX/sandbox.py"):
        self.sandbox_script = os.path.expanduser(sandbox_script)

    def _cli(self, *arguments: str) -> dict:
        """sandbox.py 관리 명령(status/up 등). 오류 JSON은 stderr로 나오므로 둘 다 확인한다."""
        result = subprocess.run(["python3.11", self.sandbox_script, *arguments], capture_output=True, text=True)
        for stream in (result.stdout, result.stderr):
            try:
                return json.loads(stream)
            except json.JSONDecodeError:
                continue
        return {"status": "ERROR", "reason": (result.stdout + result.stderr)[-500:]}

    def ensure_running(self) -> dict:
        """가짜 인터넷(fakenet)만 연결된 샌드박스를 켠다.

        network가 fakenet이 아니면 sandbox.py config로 바꾸고 up으로 다시 만든다
        (up은 바꾸기 직전의 /workspace를 스냅샷해서 새 컨테이너에 복원한다).
        """
        changed = self.network() != "fakenet"
        if changed:
            print("[Sandbox API] network를 fakenet(가짜 인터넷만 연결)으로 변경 중 (sandbox.py config)...")
            result = self._cli("config", "--network", "fakenet")
            if result.get("network") != "fakenet":
                raise RuntimeError(f"샌드박스 network를 fakenet으로 바꾸지 못했습니다: {result}")
        status = self._cli("status")
        if changed or status.get("status") != "RUNNING":
            print("[Sandbox API] 샌드박스를 켜는 중 (sandbox.py up)...")
            status = self._cli("up")
            if status.get("status") != "RUNNING":
                raise RuntimeError(f"샌드박스를 켜지 못했습니다: {status}")
        print("[Sandbox API] 샌드박스 실행 중 · network=fakenet"
              " (가짜 인터넷만 연결: sandbox.py fakenet-log 로 요청 기록 확인)")
        return status

    def network(self) -> str:
        try:
            settings = json.loads((Path(self.sandbox_script).parent / "settings.json").read_text())
            return settings.get("network", "unknown")
        except (OSError, json.JSONDecodeError):
            return "unknown"

    def execute(self, command: str) -> str:
        print("[Sandbox API] 샌드박스(sandbox.py)로 명령 전송 중...")

        cmd = [
            "python3.11",
            self.sandbox_script,
            "run",
            "--",
            "bash",
            "-c",
            command,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )

            try:
                data = json.loads(result.stdout or result.stderr)
            except json.JSONDecodeError:
                return (
                    f"[Sandbox Error] 결과 파싱 실패\n"
                    f"RAW STDOUT: {result.stdout}\n"
                    f"RAW STDERR: {result.stderr}"
                )

            reason = str(data.get("reason", ""))
            if data.get("status") == "ERROR" and ("stopped" in reason or "not running" in reason):
                # 꺼져 있음(예: 이전 명령의 시간 초과로 정지) → 켜고 한 번 다시 실행
                self.ensure_running()
                result = subprocess.run(cmd, capture_output=True, text=True)
                data = json.loads(result.stdout or result.stderr)

            status = data.get("status", "UNKNOWN")
            observation = f"Status: {status}\n"
            if data.get("reason"):
                observation += f"Reason: {data['reason']}\n"
            if data.get("exit_code") is not None:
                observation += f"Exit code: {data['exit_code']}\n"

            if data.get("stdout_tail"):
                observation += f"STDOUT:\n{data['stdout_tail'].strip()}\n"

            if data.get("stderr_tail"):
                observation += f"STDERR:\n{data['stderr_tail'].strip()}\n"

            if not data.get("stdout_tail") and not data.get("stderr_tail"):
                if data.get("exit_code", 0) == 0 and status == "FINISHED":
                    observation += (
                        "명령이 성공했습니다. 표준출력이 비어 있습니다. "
                        "파일 저장 명령은 정상적으로도 출력이 없으며, 파일이 비었다는 뜻은 아닙니다."
                    )
                else:
                    observation += "명령이 성공하지 않았거나 실행되지 않았습니다."

            return observation.strip()

        except Exception as e:
            return f"[System Error] 샌드박스 통신 실패: {str(e)}"


def extract_action(generated_text: str) -> str:
    action = parse_action(generated_text)
    return action[1] if action and action[0] == "bash" else ""


def generate_action(model, tokenizer, context, max_new_tokens=1024, max_retries=10,
                    inspection_factory=None):
    result = generate_sandbox_action(
        model, tokenizer, context, max_new_tokens=max_new_tokens,
        max_retries=max_retries, inspection_factory=inspection_factory,
    )
    return result


def choose_hook_options(input_fn=input, output_fn=print):
    """Ask for capture scope before model loading; no Hook CLI flags are needed."""
    output_fn("Hook 수집 설정을 선택하세요.")
    output_fn("1) 전체 관찰: 생성 토큰별 Hook + prompt replay/C-01 (전체 텐서는 NPY로 저장)")
    output_fn("2) 개별 설정 선택")
    try:
        choice = input_fn("선택 [1/2, 기본 1] > ").strip()
    except EOFError:
        choice = "1"
    if choice != "2":
        return "both", "summary"

    phases = {"1": "generation", "2": "replay", "3": "both"}
    output_fn("수집 범위: 1) 생성 토큰별  2) prompt replay/C-01  3) 둘 다")
    try:
        phase_choice = input_fn("범위 [1/2/3, 기본 1] > ").strip()
    except EOFError:
        phase_choice = "1"
    selected_phase = phases.get(phase_choice, "generation")

    output_fn("값 저장: 1) 전체 NPY + 요약(권장)  2) 전체 NPY + 전체 values.txt")
    try:
        values_choice = input_fn("값 형식 [1/2, 기본 1] > ").strip()
    except EOFError:
        values_choice = "1"
    selected_values = "full" if values_choice == "2" else "summary"
    return selected_phase, selected_values


def main():
    parser = argparse.ArgumentParser(description="Sandbox Agent + full module Hook inspection")
    parser.add_argument("--tensor-output", type=Path, default=Path("tensor_data"))
    parser.add_argument("--task", help="Task text; omit for interactive entry")
    parser.add_argument("--scenario", help="run one scenario from scenario_rollout.py; use 'list' to show choices")
    parser.add_argument("--condition", choices=("clear", "obstacle", "C0", "C1", "C2"),
                        help="scenario condition; prompted when --scenario is selected")
    parser.add_argument("--seed", type=int, default=0, help="scenario data and generation seed")
    parser.add_argument("--rollout-output", type=Path, default=Path("artifacts/rollouts"),
                        help="scenario rollout records directory")
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--observe-every", type=int, default=32)
    parser.add_argument("--layers", default="all", help="scenario C-01 layers: 'all' or comma-separated indexes")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--yes", action="store_true", help="skip scenario runner's workspace confirmation")
    parser.add_argument("--max-steps", type=int, default=None,
                        help="step budget (default 10; scenario runs use the harness preset's default)")
    parser.add_argument("--harness", choices=("v1", "v2"), default="v1",
                        help="scenario runs only: agent harness preset passed to scenario_rollout.py")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--max-action-retries", type=int, default=10,
                        help="Maximum generation attempts per step, including the first (default: 10)")
    parser.add_argument("--hook-phase", choices=("generation", "replay", "both"))
    parser.add_argument("--hook-values", choices=("summary", "full"), default="summary")
    args = parser.parse_args()
    if args.max_steps is not None and args.max_steps < 1:
        parser.error("max-steps must be positive")
    if args.max_new_tokens < 1 or args.max_action_retries < 1:
        parser.error("max-new-tokens and max-action-retries must be positive")
    if args.scenario is not None:
        if args.task:
            parser.error("--task is for manual runs; scenario runs take their task from the selected scenario")
        if args.scenario == "list":
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scenario_rollout import main as scenario_main

            return scenario_main(["list"])
        if args.condition is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scenario_rollout import expand_scenarios, load_scenario

            names = expand_scenarios(args.scenario)
            if len(names) != 1:
                parser.error("--scenario must name exactly one scenario (use --scenario list to see choices)")
            selected = load_scenario(names[0])
            choices = list(selected.conditions)
            print(f"{selected.scenario_id}: 사용 가능한 조건 {', '.join(choices)}")
            try:
                args.condition = input("조건을 입력하세요 > ").strip()
            except EOFError:
                parser.error("non-interactive scenario runs require --condition")
            if args.condition not in choices:
                parser.error(f"{selected.scenario_id}의 조건은 {', '.join(choices)}입니다")
        if args.hook_phase is None:
            hook_phase, hook_values = choose_hook_options()
        else:
            hook_phase, hook_values = args.hook_phase, args.hook_values

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from scenario_rollout import main as scenario_main

        command = ["run", "--scenario", args.scenario, "--condition", args.condition,
                   "--seed", str(args.seed), "--output", str(args.rollout_output),
                   "--harness", args.harness, "--max-attempts", str(args.max_action_retries),
                   "--max-new-tokens", str(args.max_new_tokens), "--command-timeout", str(args.command_timeout),
                   "--observe-every", str(args.observe_every), "--layers", args.layers,
                   "--temperature", str(args.temperature), "--top-p", str(args.top_p),
                   "--hook-output", str(args.tensor_output), "--hook-values", hook_values]
        if args.max_steps is not None:
            command += ["--max-steps", str(args.max_steps)]
        if hook_phase in ("generation", "both"):
            command.append("--hook-inspection")
        if hook_phase in ("replay", "both"):
            command.append("--hook-replay-inspection")
        if args.yes:
            command.append("--yes")
        return scenario_main(command)

    if args.condition is not None:
        parser.error("--condition requires --scenario")
    if args.hook_phase is None:
        args.hook_phase, args.hook_values = choose_hook_options()
    layers = (0, 11, 23)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from load_full_model_CUDA import load_moe_model

    tokenizer, model = load_moe_model()

    # 같은 prompt replay에서 모든 모듈 경계와 기존 C-01 특징을 함께 관찰한다.
    identity = ModelIdentity(
        model_id="openai/gpt-oss-20b",
        model_revision="local",
        tokenizer_id="openai/gpt-oss-20b",
        tokenizer_revision="local"
    )
    adapter = BlockAdapter.from_model(model, identity)
    if layers is not None:
        for layer in layers:
            adapter.block(layer)

    sandbox = RealSandboxClient()
    sandbox.ensure_running()

    trajectory_id = "task_001_seed_42"
    max_steps = args.max_steps or 10
    max_action_retries = args.max_action_retries
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]

    base_context = system_prompt_for(sandbox.network(),
                                     pages=fakenet_pages(Path(sandbox.sandbox_script).parent))

    print("=" * 80)
    print("실행할 Task를 입력하십시오.")
    print("예: 현재 sandbox의 secret.txt 내용을 읽으십시오.")
    print("=" * 80)

    user_task = args.task.strip() if args.task is not None else input("Task > ").strip()

    if not user_task:
        print("[오류] Task가 입력되지 않았습니다.")
        return

    context = [
        {"role": "system", "content": base_context},
        {"role": "user", "content": user_task},
    ]

    print("=" * 80)
    print(
        f"Agent Execution Loop 시작 "
        f"(Trajectory: {trajectory_id})"
    )
    print(f"[사용자 Task] {user_task}")
    print("=" * 80)

    for step in range(1, max_steps + 1):
        print(f"\n--- Step {step} ---")

        step_dir = args.tensor_output / run_id / f"{trajectory_id}_step_{step:03d}"

        def generation_inspection(attempt, prompt_ids, prompt_text):
            token_ids = prompt_ids[0].detach().cpu().tolist()
            return HookInspection(
                adapter, step_dir / "generation" / f"attempt_{attempt:03d}",
                layers=layers, values=args.hook_values, phase="generation",
                context={"run_id": run_id, "trajectory_id": trajectory_id, "step": step,
                         "attempt": attempt, "task_text": user_task, "prompt_text": prompt_text,
                         "input_ids": token_ids,
                         "token_pieces": tokenizer.convert_ids_to_tokens(token_ids)},
                show_console=False,
                include_non_block=False,
            )

        generated_text, action_command, input_ids, generated_token_ids, generated_token_pieces = generate_action(
            model=model,
            tokenizer=tokenizer,
            context=context,
            max_new_tokens=args.max_new_tokens,
            max_retries=max_action_retries,
            inspection_factory=generation_inspection if args.hook_phase in ("generation", "both") else None,
        )

        if generated_text is None:
            print(
                f"[오류] {max_action_retries}번의 재생성 후에도 "
                "유효한 Action을 생성하지 못했습니다."
            )
            break

        if not action_command:
            print("[Agent] stop 응답으로 작업을 종료합니다.")
            break

        # 전체 모듈 입력·출력은 같은 prefix replay에서 기록한다.
        # generation 추적은 별도 디렉터리와 attempt ID를 사용한다.
        prompt_length = input_ids.shape[1]
        action_span = ActionSpan("execute_bash", step, prompt_length, None)

        snap_context = SnapshotContext(
            run_id,
            trajectory_id,
            "interactive_task",
            "pre_action",
            action_span
        )

        print("[Hook] 동일 prompt의 pre_action replay 및 블록별 출력 수집 중...")
        inspection = (
            HookInspection(
                adapter, step_dir / "hook_inspection", layers=layers,
                values=args.hook_values, phase="pre_action_full_prefix_replay",
                context={**asdict(snap_context), "task_text": user_task, "prompt_text": tokenizer.decode(
                             input_ids[0].detach().cpu().tolist(), skip_special_tokens=False,
                             clean_up_tokenization_spaces=False),
                         "input_ids": input_ids[0].detach().cpu().tolist(),
                         "token_pieces": tokenizer.convert_ids_to_tokens(input_ids[0].detach().cpu().tolist()),
                         "generated_text": generated_text,
                         "generated_token_ids": generated_token_ids,
                         "generated_token_pieces": generated_token_pieces},
                show_console=False,
                include_non_block=False,
            ) if args.hook_phase in ("replay", "both") else nullcontext()
        )
        with inspection:
            capture = extract_prefix(
                adapter=adapter,
                input_ids=input_ids,
                context=snap_context,
                layers=layers,
                token_positions=(prompt_length - 1,),
            )
        official_save_dir = step_dir / "post_residual_block"
        write_capture(capture, official_save_dir)
        print(f"[Official Tensor] 정렬된 마지막-token 특징: {official_save_dir}")

        print(
            f"[실행할 Action]\n"
            f"{action_command}"
        )

        observation = sandbox.execute(action_command)

        print(
            f"[Sandbox 관찰 결과]\n"
            f"{observation}"
        )

        context.extend([
            {"role": "assistant", "content": f"```bash\n{action_command}\n```"},
            {"role": "user", "content": observation_message(user_task, observation)},
        ])

    print("\n루프 종료 완료.")


if __name__ == "__main__":
    main()
