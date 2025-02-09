from typing import Any, Dict

import botocore
from lib.amazon import dynamodb_client


class LibraryBuildHistory:
    def __init__(self, logger):
        self.logger = logger

    def get_lib_key(self, buildparameters_obj: Dict[str, Any], commit_hash: str):
        library = buildparameters_obj["library"]
        library_version = buildparameters_obj["library_version"]
        return f"{library}#{library_version}#{commit_hash}"

    def get_compiler_key(self, buildparameters_obj: Dict[str, Any]):
        compiler = buildparameters_obj["compiler"]
        compiler_version = buildparameters_obj["compiler_version"]
        arch = buildparameters_obj["arch"]
        libcxx = buildparameters_obj["libcxx"]

        return f"{compiler}#{compiler_version}#{arch}#{libcxx}"

    def failed(self, buildparameters_obj: Dict[str, Any], commit_hash: str):
        lib_key = self.get_lib_key(buildparameters_obj, commit_hash)
        compiler_key = self.get_compiler_key(buildparameters_obj)
        self.insert(lib_key, compiler_key, False)

    def success(self, buildparameters_obj: Dict[str, Any], commit_hash: str):
        lib_key = self.get_lib_key(buildparameters_obj, commit_hash)
        compiler_key = self.get_compiler_key(buildparameters_obj)
        self.insert(lib_key, compiler_key, True)

    def insert(self, lib_key: str, compiler_key: str, success: bool):
        try:
            dynamodb_client.put_item(
                TableName="library-build-history",
                Item={"library": {"S": lib_key}, "compiler": {"S": compiler_key}, "success": {"BOOL": success}},
            )
        except botocore.exceptions.NoCredentialsError:
            self.logger.error("Failed to insert into library-build-history (check if you've set your AWS credentials)")
            return
