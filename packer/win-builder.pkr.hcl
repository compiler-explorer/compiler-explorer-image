packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = "~> 1"
    }
  }
}

variable "MY_ACCESS_KEY" {
  type    = string
  default = ""
}

variable "MY_SECRET_KEY" {
  type    = string
  default = ""
}

data "amazon-ami" "Server2022" {
  access_key = "${var.MY_ACCESS_KEY}"
  filters = {
    name = "Windows_Server-2022-English-Core-Base-*"
  }
  most_recent = true
  owners      = ["801119661308"]
  region      = "us-east-1"
  secret_key  = "${var.MY_SECRET_KEY}"
}

locals { timestamp = regex_replace(timestamp(), "[- TZ:]", "") }

source "amazon-ebs" "Server2022" {
  access_key = "${var.MY_ACCESS_KEY}"
  ami_name             = "compiler-explorer windows packer @ ${local.timestamp}"
  communicator         = "winrm"
  iam_instance_profile = "XaniaBlog"
  instance_type        = "c5.xlarge"
  region               = "us-east-1"
  secret_key           = "${var.MY_SECRET_KEY}"
  security_group_id    = "sg-f53f9f80"
  source_ami           = "${data.amazon-ami.Server2022.id}"
  subnet_id            = "subnet-1df1e135"
  user_data_file       = "./packer/SetUpWinRM.ps1"
  vpc_id               = "vpc-17209172"
  winrm_insecure       = true
  winrm_use_ssl        = true
  winrm_username       = "Administrator"
}

build {
  sources = ["source.amazon-ebs.Server2022"]

  provisioner "powershell" {
    scripts = ["./packer/InstallPwsh.ps1", "./packer/InstallBuilderTools.ps1"]
  }

}
