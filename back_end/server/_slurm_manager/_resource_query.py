# back_end/server/_slurm_manager/_resource_query.py
"""SLURM 资源查询：scontrol/squeue 解析容量、占用、运行任务列表。"""
import json
import subprocess
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from . import logger
from ._transport import _Transport


def _resolve_default_gpu_model() -> str:
    """squeue 解析失败时的 GPU 类型 fallback。优先取 magnus_config 配置的第一
    个 GPU 的 ``value`` 字段（如 "rtx5090" / "a100"）；缺失或异常 fallback
    "unknown"。

    历史教训：之前硬编码 "rtx5090"（只 work 在某一站点），换到另一站点
    (a100) 后 cluster endpoint 把无法识别 gpu_type 的 SLURM job 显示成 rtx5090
    误导用户。读站点 config 让 fallback 跟着站点走。

    取 ``value`` 而非 ``label`` 是为了跟成功解析路径在下游 ``cluster.py`` 经
    ``.lower().replace(" ","")`` 规范化后**等价**：成功路径 gres_detail 解析
    给 "A100" / "RTX 5090"，规范化后 "a100" / "rtx5090"；本 fallback 直接给
    "a100" / "rtx5090"，规范化幂等。如果取 ``label`` ("NVIDIA GeForce RTX 5090")
    会被规范化成 "nvidiageforcertx5090"，跟前端 PHYSICAL_GPUS 枚举完全对不上。
    """
    try:
        gpus = magnus_config.get("cluster", {}).get("gpus") or []
        if gpus and isinstance(gpus[0], dict):
            value = gpus[0].get("value")
            if value:
                return str(value).lower()
    except Exception:
        pass
    return "unknown"


def _unwrap_slurm_int(value) -> int:
    """SLURM 24+ JSON 把 int 字段包成 ``{"set": bool, "infinite": bool, "number": N}``，
    21.x 还是直接 int。统一解包成 int 兼容两种 schema；解析失败或缺失返回 0。
    """
    if isinstance(value, dict):
        return value.get("number") or 0
    if isinstance(value, int):
        return value
    return 0


def _parse_tres_mem_mb(tres_str: str) -> int:
    """从 squeue ``tres_alloc_str``（形如 ``cpu=8,mem=32G,node=1,billing=8``）取
    已分配内存并换算成 MB；找不到 ``mem=`` 或解析失败返回 0。

    内存占用刻意取**已分配** TRES 而非请求值 ``memory_per_node``：后者在用户用
    ``--mem=0``（要走整机内存）时是哨兵 0，会让该 job 的内存在 cluster 视图和
    scheduler 的 free-mem 派生里凭空消失——外部 job 只显示 CPU、free-mem 被高估、
    scheduler 误判内存充足而过量提交（落到 SLURM 后卡 Resources）。
    ``tres_alloc_str`` 的 ``mem=`` 是 SLURM 解析后的实际预留量，``--mem=0`` 下
    给出整机真实数字。
    """
    for token in tres_str.split(","):
        token = token.strip()
        if token.startswith("mem="):
            try:
                return _parse_size_string(token[len("mem="):]) // (1024 ** 2)
            except (ValueError, IndexError):
                return 0
    return 0


@dataclass(frozen=True)
class NodeSnapshot:
    """一次 ``scontrol show node`` 解析得到的容量数字。

    只暴露静态容量 (total_gpus / cpu_total / mem_total_mb)。alloc 维度必须从
    ``get_all_running_tasks`` 的 job-level squeue 数字派生 (见 cluster.py /
    _decisions.py)，而非 scontrol 的 CPUAlloc / AllocMem 字段——后者在
    SLURM job 进入 COMPLETING (CG) 状态时立即清零，但 squeue 视角下 job
    仍持有 cpu / mem (跟 GPU 行为一致)。把 alloc 三维度统一从 squeue 派生
    可消除 SLURM 自身两套数据源在 CG 期间的内部错位。
    """

    total_gpus: int
    cpu_total: int
    mem_total_mb: int


class _ResourceQueryMixin:

    _transport: _Transport

    def _get_capacity_and_usage(self) -> Tuple[int, int]:
        """[Internal] 复用逻辑：获取 (总容量, 当前总占用)。"""
        try:
            # 步骤 1：scontrol 解析 Gres= 行得到 cluster GPU 总容量
            capacity_command = [
                "scontrol",
                "show",
                "node",
                "--future",
            ]
            capacity_result = self._transport.run(
                capacity_command,
                check = True,
            )

            total_capacity = 0
            for line in capacity_result.stdout.split('\n'):
                line = line.strip()
                if line.startswith("Gres=") and "gpu" in line:
                    try:
                        gres_part = line.split("Gres=")[1].split()[0].split('(')[0]
                        count = int(gres_part.split(':')[-1])
                        total_capacity += count
                    except (ValueError, IndexError):
                        pass

            # 步骤 2：squeue 解析 RUNNING + COMPLETING 任务的 GPU 申请总和得到当前总占用。
            # COMPLETING (CG) 阶段 SLURM 物理上仍把 GPU 算 Alloc 不释放给新 job，
            # 必须计入 used，否则 cluster snapshot 的 slurm_used_gpus 会在 epilog
            # 跑期间瞬时低估，跟 check_job_status (CG → RUNNING) 的语义错位。
            usage_command = [
                "squeue",
                "--states=RUNNING,COMPLETING",
                "--noheader",
                "--format=%D %b",
            ]
            usage_result = self._transport.run(
                usage_command,
                check = True,
            )

            total_allocated = 0
            for line in usage_result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    continue

                num_nodes_str, gres_req = parts[0], parts[1]
                if "gpu" not in gres_req:
                    continue

                try:
                    num_nodes = int(num_nodes_str)
                    gres_req = gres_req.split('(')[0]
                    gpu_per_node = int(gres_req.split(':')[-1])
                    total_allocated += (num_nodes * gpu_per_node)
                except (ValueError, IndexError):
                    pass

            return total_capacity, total_allocated

        except Exception as error:
            logger.error(f"Error querying cluster resources: {error}")
            return 0, 0

    def get_node_snapshot(self) -> NodeSnapshot:
        """单次 ``scontrol show node`` 拉容量数字 (GPU / CPU / 内存 total)。

        只承担 capacity 维度。alloc 维度（free / used）由调用方拿
        ``get_all_running_tasks`` 派生 sum，全部走 squeue 的 job-level 数字以
        跟 GPU 行为对齐——详见 ``NodeSnapshot`` 类 docstring。
        """
        try:
            result = self._transport.run(
                [
                    "scontrol",
                    "show",
                    "node",
                    "--future",
                ],
                check = True,
            )

            total_gpus = 0
            cpu_total = 0
            mem_total = 0

            # 故意只取容量 (CPUTot / RealMemory)；CPUAlloc / AllocMem 在 CG 期间
            # 不可信，alloc 维度全部从 get_all_running_tasks 派生。详见 NodeSnapshot
            # docstring。
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith("Gres=") and "gpu" in line:
                    try:
                        gres_part = line.split("Gres=")[1].split()[0].split('(')[0]
                        total_gpus += int(gres_part.split(':')[-1])
                    except (ValueError, IndexError):
                        pass
                if "CPUTot=" in line:
                    # 同行布局：CPUAlloc=34 CPUEfctv=192 CPUTot=192 CPULoad=...
                    for part in line.split():
                        if part.startswith("CPUTot="):
                            cpu_total += int(part.split("=")[1])
                elif line.startswith("RealMemory="):
                    # 同行布局：RealMemory=515000 AllocMem=102400 ...
                    for part in line.split():
                        if part.startswith("RealMemory="):
                            mem_total += int(part.split("=")[1])

            return NodeSnapshot(
                total_gpus = total_gpus,
                cpu_total = cpu_total,
                mem_total_mb = mem_total,
            )
        except Exception as error:
            logger.error(f"Error querying node snapshot: {error}")
            return NodeSnapshot(
                total_gpus = 0,
                cpu_total = 0,
                mem_total_mb = 0,
            )

    def get_partition_snapshot(self, partition: str) -> NodeSnapshot:
        """单次 ``scontrol show node --oneliner`` 拉**指定分区**节点的容量数字
        (GPU / CPU / 内存 total)。

        共享集群租户模式的 cluster 视图用：那里资源总量该是本租户所在分区的实际容量
        （租户在此分区里和别人竞争），而不是整集群所有分区之和。和 ``get_node_snapshot``
        一样只承担 capacity 维度，alloc（free / used）由调用方拿 ``get_all_running_tasks``
        派生 sum（详见 ``NodeSnapshot`` docstring）。

        用 ``--oneliner`` 让每个节点占一行，便于按 ``Partitions=`` 字段过滤；一个节点
        可同属多个分区，只要其分区列表含目标分区就计入容量。
        """
        try:
            result = self._transport.run(
                [
                    "scontrol",
                    "show",
                    "node",
                    "--oneliner",
                ],
                check = True,
            )

            total_gpus = 0
            cpu_total = 0
            mem_total = 0

            for line in result.stdout.split('\n'):
                line = line.strip()
                if not line:
                    continue

                # --oneliner 一行一节点，字段是空格分隔的 key=value。逐 token 拆成字典，
                # 只取关心的单 token 值（CPUTot / RealMemory / Gres / Partitions）——含空格
                # 的值（如 Reason="..."）会被拆散，但不影响这几个字段的取值。
                fields: Dict[str, str] = {}
                for token in line.split():
                    if "=" in token:
                        key, value = token.split("=", 1)
                        fields[key] = value

                node_partitions = fields.get("Partitions", "").split(",")
                if partition not in node_partitions:
                    continue

                try:
                    cpu_total += int(fields.get("CPUTot", "0"))
                except ValueError:
                    pass
                try:
                    mem_total += int(fields.get("RealMemory", "0"))
                except ValueError:
                    pass

                gres = fields.get("Gres", "")
                if "gpu" in gres:
                    try:
                        total_gpus += int(gres.split('(')[0].split(':')[-1])
                    except (ValueError, IndexError):
                        pass

            return NodeSnapshot(
                total_gpus = total_gpus,
                cpu_total = cpu_total,
                mem_total_mb = mem_total,
            )
        except Exception as error:
            logger.error(f"Error querying partition snapshot for '{partition}': {error}")
            return NodeSnapshot(
                total_gpus = 0,
                cpu_total = 0,
                mem_total_mb = 0,
            )

    def has_schedulable_node(self) -> bool:
        """集群里是否至少有一个节点处于可调度状态。

        可调度 = base state ∈ {IDLE, MIXED, ALLOCATED} 且无 DRAIN/DOWN/MAINT/FAIL/
        REBOOT 修饰。给 submit 入口的 precheck 用：杜绝节点全部 drain/reboot 期间
        用户提交"看似成功但永不开始"的 ghost job (前端反复点提交都返回 200，DB 堆
        积一片 PENDING，cluster 视图被污染)。

        失败时 fail-open 返回 True：让 SLURM 自己 PENDING 兜底，不引入新故障路径。
        """
        try:
            result = self._transport.run(
                [
                    "scontrol",
                    "show",
                    "node",
                    "--oneliner",
                ],
                check = True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(f"has_schedulable_node fail-open: scontrol failed: {error}")
            return True

        bad_modifiers = {
            "DRAIN", "DRAINING", "DRAINED",
            "DOWN", "DOWNED",
            "MAINT",
            "FAIL", "FAILING",
            "REBOOT_REQUESTED", "REBOOT_ISSUED",
            "NOT_RESPONDING",
        }
        ok_base = {"IDLE", "MIXED", "ALLOCATED"}

        for line in result.stdout.split('\n'):
            line = line.strip()
            if not line:
                continue
            for token in line.split():
                if not token.startswith("State="):
                    continue
                state = token.split('=', 1)[1].upper()
                base = state.split('+')[0]
                modifiers = set(state.split('+')[1:])
                if base in ok_base and not (modifiers & bad_modifiers):
                    return True
                break

        return False

    def get_cluster_free_gpus(self) -> int:
        cap, alloc = self._get_capacity_and_usage()
        return max(0, cap - alloc)

    def get_resource_snapshot(self) -> Dict:
        cap, alloc = self._get_capacity_and_usage()
        return {
            "total_gpus": cap,
            "slurm_used_gpus": alloc,
        }

    def check_job_status(self, slurm_job_id: str) -> str:
        """查询 SLURM 任务状态。

        注意：squeue 查不到时默认视为 COMPLETED，因为 FAILED 通常会残留记录。
        """
        command = [
            "squeue",
            "-h",
            "-j", slurm_job_id,
            "-o", "%t",
        ]

        try:
            result = self._transport.run(
                command,
            )
            state = result.stdout.strip()

            if not state:
                return "COMPLETED"

            # R=Running, PD=Pending, CG=Completing, CD=Completed, F=Failed,
            # CA=Cancelled, TO=Timeout
            mapping = {
                "R": "RUNNING",
                "PD": "PENDING",
                "CG": "RUNNING",
                "CD": "COMPLETED",
                "F": "FAILED",
                "CA": "FAILED",
                "TO": "FAILED",
            }
            return mapping.get(state, "UNKNOWN")

        except Exception as error:
            logger.error(f"Failed to check job status {slurm_job_id}: {error}")
            return "UNKNOWN"

    def get_all_running_tasks(self, partition: Optional[str] = None) -> List[Dict]:
        """获取所有正在运行 / 收尾中的 SLURM 任务详情。

        包含 RUNNING 与 COMPLETING：CG 阶段 SLURM 物理上仍占 GPU 不释放，且本类
        ``check_job_status`` 也把 CG 映射到 RUNNING；这里跟它对齐，避免 cluster
        endpoint 跟 sync_reality 对同一个 job 给出 "已释放 vs 仍在跑" 的不一致快照。
        epilog 较长（如做单卡 reset 兜底）时 CG 会停留数十秒，否则瞬时不可见。

        ``partition`` 非空时只查该分区的任务（squeue ``--partition=<name>``）：共享集群
        租户模式下 magnus 只在自己获授的分区里和别人竞争资源，整集群全分区的任务既不是
        这个视图该呈现的、也会把列表淹没；限定到本分区既给出真实的同分区竞争态、又不越界。
        独占集群（authoritative）传 None 看全量。

        关键坑点：SLURM job-completion caching 让 ``squeue --json`` 可能返回内存中
        残留的已结束任务（即便用了 ``--states`` 过滤）。代码层面必须再显式检查
        ``job_state`` 是否真的命中 RUNNING / COMPLETING，不能信任 SLURM 自己的过滤。
        """
        default_gpu_model = _resolve_default_gpu_model()
        command = [
            "squeue",
            "--states=RUNNING,COMPLETING",
            "--json",
        ]
        if partition:
            command.append(f"--partition={partition}")

        try:
            result = self._transport.run(
                command,
                check = True,
            )
            data = json.loads(result.stdout)

            tasks = []

            for job in data.get("jobs", []):
                try:
                    states = job.get("job_state", [])
                    if "RUNNING" not in states and "COMPLETING" not in states:
                        continue

                    job_id = str(job.get("job_id"))
                    user = job.get("user_name")
                    name = job.get("name")

                    # SLURM 21.x JSON 给 int，24+ 给 {"set","infinite","number"}；
                    # _unwrap_slurm_int 兼容两种 schema。下面 cpu / mem / node_count
                    # 同款处理。
                    start_ts = _unwrap_slurm_int(job.get("start_time"))
                    if start_ts:
                        start_time_str = datetime.fromtimestamp(start_ts).isoformat()
                    else:
                        start_time_str = datetime.now().isoformat()

                    # 优先解析 gres_detail，例如 "gpu:rtx5090:1(IDX:0)"
                    gpu_count = 0
                    gpu_type = default_gpu_model
                    gres_details = job.get("gres_detail", [])

                    for item in gres_details:
                        if "gpu" not in item:
                            continue

                        parts = item.split(':')

                        try:
                            # parts[-1] 形如 "1(IDX:0)"，取括号前作为数量
                            count_str = parts[-1].split('(')[0]
                            count = int(count_str)
                            gpu_count += count
                        except (ValueError, IndexError):
                            pass

                        if len(parts) >= 3:
                            model_raw = parts[1]
                            if model_raw.lower().startswith("rtx"):
                                gpu_type = model_raw.upper().replace("RTX", "RTX ")
                            else:
                                gpu_type = model_raw.upper()

                    # Fallback: gres_detail 缺失时退回 tres_per_node
                    if gpu_count == 0:
                        tres = job.get("tres_per_node", "")
                        if "gpu" in tres:
                            try:
                                count = int(tres.split(':')[-1])
                                gpu_count = count
                            except ValueError:
                                pass

                    # CPU / Mem 占用走 squeue (job-level)，理由见 NodeSnapshot
                    # docstring：CG 期间这两个字段仍报 alloc，跟 GPU 行为对齐。
                    # ``cpus`` 是 job 已分配的逻辑 CPU 总数 (跨节点之和，hyperthread
                    # 已 round-up)。
                    cpu_count = _unwrap_slurm_int(job.get("cpus"))

                    # 内存取已分配 TRES (tres_alloc_str 的 mem=)，而非请求值
                    # memory_per_node——后者在 --mem=0 (要走整机内存) 时为哨兵 0，
                    # 会让内存凭空消失、cluster/scheduler 的 free-mem 被高估，详见
                    # _parse_tres_mem_mb docstring。tres_alloc_str 缺失或无 mem=
                    # 时（旧版 SLURM）fallback 回 memory_per_node * node_count。
                    memory_mb = _parse_tres_mem_mb(job.get("tres_alloc_str") or "")
                    if memory_mb <= 0:
                        mem_per_node = _unwrap_slurm_int(job.get("memory_per_node"))
                        node_count = _unwrap_slurm_int(job.get("node_count")) or 1
                        memory_mb = mem_per_node * node_count

                    tasks.append({
                        "id": job_id,
                        "user": user,
                        "name": name,
                        "start_time": start_time_str,
                        "gpu_count": gpu_count,
                        "gpu_type": gpu_type,
                        "cpu_count": cpu_count,
                        "memory_mb": memory_mb,
                    })

                except Exception as error:
                    logger.warning(
                        f"Failed to parse job json: {error}\n"
                        f"Traceback:\n{traceback.format_exc()}"
                    )
                    continue

            return tasks

        except Exception as error:
            logger.error(f"Failed to query running tasks (json): {error}")
            return []
