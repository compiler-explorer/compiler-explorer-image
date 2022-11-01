import functools
import logging
import socket
from multiprocessing.pool import ThreadPool
from typing import Dict, Optional

import paramiko.ssh_exception

from lib.amazon import ec2, ec2_client, as_client, elb_client, get_releases, release_for
from lib.ssh import exec_remote, can_ssh_to

STATUS_FORMAT = "{: <16} {: <20} {: <10} {: <12} {: <11} {: <11} {: <14}"
logger = logging.getLogger("instance")


@functools.lru_cache()
def _singleton_instance(name: str):
    result = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name", "Values": ["stopped", "stopping", "running", "pending"]},
        ]
    )
    reservations = result["Reservations"]
    if len(reservations) == 0:
        raise RuntimeError(f"No instance named '{name}' found")
    if len(reservations) > 1:
        raise RuntimeError(f"Multiple instances named '{name}' found ({reservations}")
    instances = reservations[0]["Instances"]
    if len(instances) == 0:
        raise RuntimeError(f"No instance named '{name}' found")
    if len(instances) > 1:
        raise RuntimeError(f"Multiple instances named '{name}' found ({instances}")
    return ec2.Instance(id=instances[0]["InstanceId"])


class Instance:
    def __init__(self, health, group_arn):
        self.group_arn = group_arn
        self.instance = ec2.Instance(id=health["Target"]["Id"])
        self.elb_health = "unknown"
        self.service_status = {"SubState": "unknown"}
        self.running_version = "unknown"
        self.update(health)

    def __str__(self):
        return "{}@{}".format(self.instance.id, self.instance.private_ip_address)

    def describe_autoscale(self) -> Optional[Dict]:
        results = as_client.describe_auto_scaling_instances(InstanceIds=[self.instance.instance_id])[
            "AutoScalingInstances"
        ]
        if not results:
            return None
        return results[0]

    def update(self, health=None):
        if not health:
            health = elb_client.describe_target_health(
                TargetGroupArn=self.group_arn, Targets=[{"Id": self.instance.instance_id}]
            )["TargetHealthDescriptions"][0]
        self.instance.load()
        self.elb_health = health["TargetHealth"]["State"]
        if can_ssh_to(self):
            try:
                self.service_status = {
                    key: value
                    for key, value in (
                        s.split("=", 1)
                        for s in exec_remote(self, ["sudo", "systemctl", "show", "compiler-explorer"]).split("\n")
                        if "=" in s
                    )
                }
                self.running_version = exec_remote(
                    self, ["bash", "-c", "if [[ -f /infra/.deploy/s3_key ]]; then cat /infra/.deploy/s3_key; fi"]
                ).strip()
            except (socket.error, paramiko.ssh_exception.SSHException) as e:
                logger.warning("Failed to execute on remote host: %s", e)

    @staticmethod
    def elb_instances(group_arn):
        with ThreadPool(processes=16) as pool:
            return pool.map(
                lambda h: Instance(h, group_arn),
                elb_client.describe_target_health(TargetGroupArn=group_arn)["TargetHealthDescriptions"],
            )


class AdminInstance:
    def __init__(self, instance):
        self.instance = instance
        self.elb_health = "unknown"
        self.service_status = {"SubState": "unknown"}
        self.running_version = "admin"

    @property
    def address(self):
        return self.instance.public_ip_address

    @staticmethod
    def instance():
        return AdminInstance(_singleton_instance("AdminNode"))


class ConanInstance:
    def __init__(self, instance):
        self.instance = instance
        self.elb_health = "unknown"
        self.service_status = {"SubState": "unknown"}
        self.running_version = "conan"

    @staticmethod
    def instance():
        return ConanInstance(_singleton_instance("ConanNode"))


class BuilderInstance:
    def __init__(self, instance):
        self.instance = instance
        self.elb_health = "unknown"
        self.service_status = {"SubState": "unknown"}
        self.running_version = "builder"

    @staticmethod
    def instance():
        return BuilderInstance(_singleton_instance("Builder"))

    def start(self):
        self.instance.start()

    def stop(self):
        self.instance.stop()

    def status(self):
        self.instance.load()
        return self.instance.state["Name"]


class RunnerInstance:
    def __init__(self, instance):
        self.instance = instance
        self.elb_health = "unknown"
        self.service_status = {"SubState": "unknown"}
        self.running_version = "runner"

    @staticmethod
    def instance():
        return RunnerInstance(_singleton_instance("CERunner"))

    def start(self):
        self.instance.start()

    def stop(self):
        self.instance.stop()

    def status(self):
        self.instance.load()
        return self.instance.state["Name"]


def print_instances(instances, number=False):
    if number:
        print("   ", end="")
    releases = get_releases()
    print(STATUS_FORMAT.format("Address", "Instance Id", "State", "Type", "ELB", "Service", "Version"))
    count = 0
    for inst in instances:
        if number:
            print("{: <3}".format(count), end="")
        count += 1
        running_version = release_for(releases, inst.running_version)
        if running_version:
            running_version = "{} ({})".format(running_version.version, running_version.branch)
        else:
            running_version = "(unknown {})".format(inst.running_version)
        print(
            STATUS_FORMAT.format(
                inst.instance.private_ip_address,
                inst.instance.id,
                inst.instance.state["Name"],
                inst.instance.instance_type,
                inst.elb_health,
                inst.service_status["SubState"],
                running_version,
            )
        )
