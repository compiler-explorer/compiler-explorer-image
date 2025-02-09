from datetime import datetime
from operator import attrgetter
from typing import List, Optional
import logging
import json

from lib.env import Config, Environment
from lib.releases import Version, Release, Hash, VersionSource

S3_STORAGE_BUCKET = "storage.godbolt.org"


class LazyObjectWrapper:
    def __init__(self, fn):
        self.__fn = fn
        self.__setup = False
        self.__obj = None

    def __ensure_setup(self):
        if not self.__setup:
            self.__obj = self.__fn()
            self.__setup = True

    def __getattr__(self, attr):
        self.__ensure_setup()
        return getattr(self.__obj, attr)


# this is a free function to avoid potentially shadowing any underlying members
# which could happen if this was itself placed as a member of LazyObjectWrapper
def force_lazy_init(lazy):
    # pylint: disable=W0212
    lazy._LazyObjectWrapper__ensure_setup()


def _import_boto():
    obj = __import__("boto3")

    if not obj.session.Session().region_name:
        obj.setup_default_session(region_name="us-east-1")

    return obj


botocore = LazyObjectWrapper(lambda: __import__("botocore"))
boto3 = LazyObjectWrapper(_import_boto)


def _create_anon_s3_client():
    # https://github.com/boto/botocore/issues/1395
    obj = boto3.client("s3", aws_access_key_id="", aws_secret_access_key="")
    # pylint: disable=W0212
    obj._request_signer.sign = lambda *args, **kwargs: None
    return obj


ec2 = LazyObjectWrapper(lambda: boto3.resource("ec2"))
ec2_client = LazyObjectWrapper(lambda: boto3.client("ec2"))
s3 = LazyObjectWrapper(lambda: boto3.resource("s3"))
as_client = LazyObjectWrapper(lambda: boto3.client("autoscaling"))
elb_client = LazyObjectWrapper(lambda: boto3.client("elbv2"))
s3_client = LazyObjectWrapper(lambda: boto3.client("s3"))
anon_s3_client = LazyObjectWrapper(_create_anon_s3_client)
dynamodb_client = LazyObjectWrapper(lambda: boto3.client("dynamodb"))
ssm_client = LazyObjectWrapper(lambda: boto3.client("ssm"))
LINKS_TABLE = "links"
VERSIONS_LOGGING_TABLE = "versionslog"


def target_group_for(cfg: Config) -> dict:
    result = elb_client.describe_target_groups(Names=[cfg.env.value.capitalize()])
    if len(result["TargetGroups"]) != 1:
        raise RuntimeError(f"Invalid environment {cfg.env.value}")
    return result["TargetGroups"][0]


def target_group_arn_for(cfg: Config) -> str:
    return target_group_for(cfg)["TargetGroupArn"]


def get_autoscaling_group(group_name):
    result = as_client.describe_auto_scaling_groups(AutoScalingGroupNames=[group_name])
    return result["AutoScalingGroups"][0]


def get_autoscaling_groups_for(cfg: Config) -> List[dict]:
    result = list(
        filter(
            lambda r: cfg.env.value.lower() == r["AutoScalingGroupName"],
            as_client.describe_auto_scaling_groups()["AutoScalingGroups"],
        )
    )
    if not result:
        raise RuntimeError(f"Invalid environment {cfg.env.value}")
    return result


def s3_file_exists(key: str) -> bool:
    try:
        s3_client.head_object(Bucket="compiler-explorer", Key=key)
        return True
    except s3_client.exceptions.ClientError:
        return False


def get_key_counterpart(key: str) -> str:
    if key.endswith(".tar.xz"):
        return key.replace(".tar.xz", ".zip")
    elif key.endswith(".zip"):
        return key.replace(".zip", ".tar.xz")

    return key


def remove_release(release: Release) -> None:
    files_to_delete = [release.key, release.static_key, release.info_key]

    counterpart = get_key_counterpart(release.key)
    if s3_file_exists(counterpart):
        files_to_delete += counterpart

    if release.static_key is not None:
        counterpart_static = get_key_counterpart(release.static_key)
        if s3_file_exists(counterpart_static):
            files_to_delete += counterpart_static

    s3_client.delete_objects(
        Bucket="compiler-explorer",
        Delete=dict(Objects=[dict(Key=key) for key in files_to_delete if key is not None]),
    )


def _get_releases(source: VersionSource, prefix: str, archive_extension: str = ".tar.xz") -> List[Release]:
    paginator = s3_client.get_paginator("list_objects_v2")
    result_iterator = paginator.paginate(Bucket="compiler-explorer", Prefix=prefix)

    staticfiles = {}
    releases = {}
    for result in result_iterator.search("[Contents][]"):
        key = result["Key"]
        if not key.endswith(archive_extension):
            continue
        split_key = key.split("/")
        branch = "/".join(split_key[2:-1])
        version_str = split_key[-1].split(".")[0]
        version = Version.from_string(version_str, source)

        if key.endswith(".static" + archive_extension):
            staticfiles[version] = key
            continue

        size = result["Size"]
        info_key = "/".join(split_key[:-1]) + "/" + version_str + ".txt"
        try:
            o = s3_client.get_object(Bucket="compiler-explorer", Key=info_key)
        except s3_client.exceptions.NoSuchKey:
            logging.warning("Ignoring broken key %s", key)
            continue
        release_hash = Hash(o["Body"].read().decode("utf-8").strip())
        releases[version] = Release(version, branch, key, info_key, size, release_hash)

    for ver, key in staticfiles.items():
        r = releases.get(ver)
        if r:
            r.static_key = key

    return list(releases.values())


def get_releases(cfg: Config) -> List[Release]:
    if cfg.env.is_windows:
        return _get_releases(VersionSource.GITHUB, "dist/gh", ".zip")
    else:
        return _get_releases(VersionSource.TRAVIS, "dist/travis") + _get_releases(VersionSource.GITHUB, "dist/gh")


def get_all_releases() -> List[Release]:
    return (
        _get_releases(VersionSource.TRAVIS, "dist/travis")
        + _get_releases(VersionSource.GITHUB, "dist/gh")
        + _get_releases(VersionSource.GITHUB, "dist/gh", ".zip")
    )


def get_tools_releases() -> List[Release]:
    return _get_releases(VersionSource.TRAVIS, "dist/tools")


def download_release_file(file, destination):
    s3_client.download_file("compiler-explorer", file, destination)


def download_release_fileobj(key, fobj):
    s3_client.download_fileobj("compiler-explorer", key, fobj)


def find_release(cfg: Config, version: Version) -> Optional[Release]:
    for r in get_releases(cfg):
        if r.version == version:
            return r
    return None


def find_latest_release(cfg: Config, branch: str) -> Optional[Release]:
    releases = [release for release in get_releases(cfg) if branch == "" or release.branch == branch]
    return max(releases, key=attrgetter("version")) if len(releases) > 0 else None


def get_current_key(cfg: Config) -> Optional[str]:
    try:
        o = s3_client.get_object(Bucket="compiler-explorer", Key=cfg.env.version_key)
        return o["Body"].read().decode("utf-8").strip()
    except s3_client.exceptions.NoSuchKey:
        return None


def get_all_current() -> List[str]:
    versions = []
    for env in [env for env in Environment if env.keep_builds]:
        try:
            o = s3_client.get_object(Bucket="compiler-explorer", Key=env.version_key)
            versions.append(o["Body"].read().decode("utf-8").strip())
        except s3_client.exceptions.NoSuchKey:
            pass
    return versions


def set_current_key(cfg: Config, key: str):
    s3_key = cfg.env.version_key
    print("Setting {} to {}".format(s3_key, key))
    s3_client.put_object(Bucket="compiler-explorer", Key=s3_key, Body=key, ACL="public-read")


def release_for(releases: List[Release], s3_key: str) -> Optional[Release]:
    for r in releases:
        if r.key == s3_key:
            return r
    return None


def get_current_release(cfg: Config) -> Optional[Release]:
    current = get_current_key(cfg)
    return release_for(get_releases(), current) if current else None


def get_events_file(cfg: Config) -> str:
    try:
        o = s3_client.get_object(Bucket="compiler-explorer", Key=events_file_for(cfg))
        return o["Body"].read().decode("utf-8")
    except s3_client.exceptions.NoSuchKey:
        return "{}"


def save_event_file(cfg: Config, contents: str):
    s3_client.put_object(
        Bucket="compiler-explorer",
        Key=events_file_for(cfg),
        Body=contents,
        ACL="public-read",
        CacheControl="public, max-age=60",
        ContentType="application/json",
    )


def events_file_for(cfg: Config):
    events_file = "motd/motd-{}.json".format(cfg.env.value)
    return events_file


def get_short_link(short_id: str) -> dict:
    result = dynamodb_client.get_item(
        TableName=LINKS_TABLE,
        Key={"prefix": {"S": short_id[:6]}, "unique_subhash": {"S": short_id}},
        ConsistentRead=True,
    )
    return result.get("Item")


def expand_short_link(short_id: str) -> dict:
    item = get_short_link(short_id)
    key = "state/" + item["full_hash"]["S"]
    result = s3_client.get_object(Bucket=S3_STORAGE_BUCKET, Key=key)
    return json.loads(result["Body"].read().decode("utf-8"))


def put_short_link(item):
    dynamodb_client.put_item(TableName=LINKS_TABLE, Item=item)


def delete_short_link(item):
    dynamodb_client.delete_item(TableName=LINKS_TABLE, Key={"prefix": {"S": item[:6]}, "unique_subhash": {"S": item}})


def log_new_build(cfg: Config, new_version):
    current_time = datetime.utcnow().isoformat()
    new_item = {"buildId": {"S": new_version}, "timestamp": {"S": current_time}, "env": {"S": cfg.env.value}}
    dynamodb_client.put_item(TableName=VERSIONS_LOGGING_TABLE, Item=new_item)


def print_version_logs(items):
    for item in items:
        print("{} (from {}) at {}".format(item["buildId"]["S"], item["env"]["S"], item["timestamp"]["S"]))


def list_all_build_logs(cfg: Config):
    result = dynamodb_client.scan(
        TableName=VERSIONS_LOGGING_TABLE,
        FilterExpression="env = :environment",
        ExpressionAttributeValues={":environment": {"S": cfg.env.value}},
    )
    print_version_logs(result.get("Items", []))


def list_period_build_logs(cfg: Config, from_time: Optional[str], until_time: Optional[str]):
    result = None
    if from_time is None and until_time is None:
        #  Our only calling site already checks this, but added as fallback just in case
        list_all_build_logs(cfg)
    elif from_time is None:
        assert until_time is not None, "Required field --until is missing"
        result = dynamodb_client.scan(
            TableName=VERSIONS_LOGGING_TABLE,
            FilterExpression="#ts <= :until and env = :environment",
            ExpressionAttributeValues={":until": {"S": until_time}, ":environment": {"S": cfg.env.value}},
            ExpressionAttributeNames={"#ts": "timestamp"},
        )
    elif until_time is None:
        assert from_time is not None, "Required field --from is missing"
        result = dynamodb_client.scan(
            TableName=VERSIONS_LOGGING_TABLE,
            FilterExpression="#ts >= :from and env = :environment",
            ExpressionAttributeValues={":from": {"S": from_time}, ":environment": {"S": cfg.env.value}},
            ExpressionAttributeNames={"#ts": "timestamp"},
        )
    else:
        assert until_time is not None and from_time is not None, "Expected both --until and --from to be filled"
        result = dynamodb_client.scan(
            TableName=VERSIONS_LOGGING_TABLE,
            FilterExpression="#ts BETWEEN :from AND :until and env = :environment",
            ExpressionAttributeValues={
                ":until": {"S": until_time},
                ":from": {"S": from_time},
                ":environment": {"S": cfg.env.value},
            },
            ExpressionAttributeNames={"#ts": "timestamp"},
        )
    if result is not None:
        print_version_logs(result.get("Items", []))


def delete_s3_links(items):
    s3_client.delete_objects(Bucket=S3_STORAGE_BUCKET, Delete={"Objects": [{"Key": item} for item in items]})


def list_short_links():
    s3_paginator = s3_client.get_paginator("list_objects_v2")
    db_paginator = dynamodb_client.get_paginator("scan")
    return (
        s3_paginator.paginate(Bucket=S3_STORAGE_BUCKET, Prefix="state/"),
        db_paginator.paginate(TableName=LINKS_TABLE, ProjectionExpression="unique_subhash, full_hash, creation_ip"),
    )


def list_compilers(with_extension=False):
    s3_paginator = anon_s3_client.get_paginator("list_objects_v2")
    prefix = "opt/"
    for page in s3_paginator.paginate(Bucket="compiler-explorer", Prefix=prefix):
        for compiler in page["Contents"]:
            name = compiler["Key"][len(prefix) :]
            if with_extension:
                yield name
            else:
                found = name.find(".tar")
                if found <= 0:
                    continue
                name = name[:found]
                yield name


def list_s3_artifacts(bucket, prefix):
    s3_paginator = anon_s3_client.get_paginator("list_objects_v2")
    for page in s3_paginator.paginate(Bucket=bucket, Prefix=prefix):
        if page["KeyCount"]:
            for match in page["Contents"]:
                yield match["Key"]


def get_ssm_param(param):
    return ssm_client.get_parameter(Name=param)["Parameter"]["Value"]


def bouncelock_file_for(cfg: Config):
    return f"ce-bouncelock-{cfg.env.value}"


def put_bouncelock_file(cfg: Config):
    s3_client.put_object(
        Bucket="compiler-explorer",
        Key=bouncelock_file_for(cfg),
        Body="",
        ACL="public-read",
        CacheControl="public, max-age=60",
        ContentType="text/plain",
    )


def delete_bouncelock_file(cfg: Config):
    s3_client.delete_object(Bucket="compiler-explorer", Key=bouncelock_file_for(cfg))


def has_bouncelock_file(cfg: Config):
    try:
        s3_client.get_object(Bucket="compiler-explorer", Key=bouncelock_file_for(cfg))
        return True
    except s3_client.exceptions.NoSuchKey:
        return False


def notify_file_name():
    return "ce-notify-file"


def has_notify_file():
    try:
        s3_client.head_object(Bucket="compiler-explorer", Key=notify_file_name())
        return True
    except (s3_client.exceptions.NoSuchKey, botocore.exceptions.ClientError):
        return False


def put_notify_file(body: str):
    s3_client.put_object(
        Bucket="compiler-explorer", Key=notify_file_name(), Body=body, ACL="public-read", ContentType="text/plain"
    )


def delete_notify_file():
    s3_client.delete_object(Bucket="compiler-explorer", Key=notify_file_name())


def set_current_notify(sha: str):
    if not has_notify_file():
        put_notify_file(sha)


def get_current_notify():
    try:
        o = s3_client.get_object(Bucket="compiler-explorer", Key=notify_file_name())
        return o["Body"].read().decode("utf-8")
    except s3_client.exceptions.NoSuchKey:
        return None
