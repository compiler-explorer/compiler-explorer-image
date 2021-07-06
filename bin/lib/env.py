from dataclasses import dataclass
from enum import Enum


class Environment(Enum):
    PROD = 'prod'
    BETA = 'beta'
    STAGING = 'staging'


@dataclass(frozen=True)
class Config:
    env: Environment
    use_mosh: bool
