resource "aws_efs_file_system" "fs-db4c8192" {
  creation_token   = "console-200306b4-68be-436f-9c7a-2dc3280116a4"
  performance_mode = "generalPurpose"
  tags             = {
    Name = "CompilerExplorer"
  }
  lifecycle_policy {
    transition_to_ia = "AFTER_14_DAYS"
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_efs_mount_target" "fs-db4c8192" {
  for_each        = local.subnet_mappings
  file_system_id  = aws_efs_file_system.fs-db4c8192.id
  subnet_id       = module.ce_network.subnet[each.key].id
  security_groups = [aws_security_group.efs.id]
}
