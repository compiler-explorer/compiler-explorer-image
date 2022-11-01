locals {
  runner_image_id  = "ami-0cc6fd5f52bd05b88"
  conan_image_id   = "ami-0b41dc7a318b530bd"
  builder_image_id = "ami-0ef4921e9d82c03fb"
  admin_subnet     = module.ce_network.subnet["1a"].id
}

resource "aws_instance" "AdminNode" {
  ami                         = "ami-0e76b49ef537405f1"
  iam_instance_profile        = aws_iam_instance_profile.CompilerExplorerAdminNode.name
  ebs_optimized               = false
  instance_type               = "t3a.small"
  monitoring                  = false
  key_name                    = "mattgodbolt"
  subnet_id                   = local.admin_subnet
  vpc_security_group_ids      = [aws_security_group.AdminNode.id]
  associate_public_ip_address = true
  source_dest_check           = true

  root_block_device {
    volume_type           = "gp2"
    volume_size           = 24
    delete_on_termination = false
  }

  tags = {
    Name = "AdminNode"
  }

  volume_tags = {
    Name = "AdminNodeVolume"
    Site = "CompilerExplorer"
  }
}

resource "aws_instance" "ConanNode" {
  ami                         = local.conan_image_id
  iam_instance_profile        = aws_iam_instance_profile.CompilerExplorerRole.name
  ebs_optimized               = false
  instance_type               = "t2.micro"
  monitoring                  = false
  key_name                    = "mattgodbolt"
  subnet_id                   = local.admin_subnet
  vpc_security_group_ids      = [aws_security_group.CompilerExplorer.id]
  associate_public_ip_address = true
  source_dest_check           = false

  root_block_device {
    volume_type           = "gp2"
    volume_size           = 10
    delete_on_termination = true
  }

  tags = {
    Name = "ConanNode"
  }

  volume_tags = {
    Name = "CEConanServerVol1"
    Site = "CompilerExplorer"
  }
}

resource "aws_volume_attachment" "ebs_conanserver" {
  device_name = "/dev/xvdb"
  volume_id   = "vol-0a99526fcf7bcfc11"
  instance_id = aws_instance.ConanNode.id
}


resource "aws_instance" "BuilderNode" {
  ami                         = local.builder_image_id
  iam_instance_profile        = aws_iam_instance_profile.Builder.name
  ebs_optimized               = true
  instance_type               = "c5d.4xlarge"
  monitoring                  = false
  key_name                    = "mattgodbolt"
  subnet_id                   = local.admin_subnet
  vpc_security_group_ids      = [aws_security_group.Builder.id]
  associate_public_ip_address = true
  source_dest_check           = false
  user_data                   = "builder"

  root_block_device {
    volume_type           = "gp2"
    volume_size           = 24
    delete_on_termination = true
  }

  lifecycle {
    ignore_changes = [
      // Seemingly needed to not replace stopped instances
      associate_public_ip_address
    ]
  }

  tags = {
    Name = "Builder"
  }
}

resource "aws_instance" "CERunner" {
  ami                         = local.runner_image_id
  iam_instance_profile        = aws_iam_instance_profile.CompilerExplorerRole.name
  ebs_optimized               = false
  instance_type               = "c5.large"
  monitoring                  = false
  key_name                    = "mattgodbolt"
  subnet_id                   = local.admin_subnet
  vpc_security_group_ids      = [aws_security_group.CompilerExplorer.id]
  associate_public_ip_address = true
  source_dest_check           = false
  user_data                   = "runner"

  root_block_device {
    volume_type           = "gp2"
    volume_size           = 24
    delete_on_termination = true
  }

  lifecycle {
    ignore_changes = [
      // Seemingly needed to not replace stopped instances
      associate_public_ip_address
    ]
  }

  tags = {
    Name = "CERunner"
  }
}
