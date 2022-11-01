module ce_network {
  source        = "./modules/ce_network"
  cidr_b_prefix = "172.30"
  subnets       = local.subnet_mappings
}

locals {
  // Hackily we inject this into the module instead of reading it out. Mainly due to frustration with a foreach()
  subnet_mappings = {
    "1a" : "0",
    "1b" : "1",
    "1c" : "4",
    "1d" : "2",
    "1f" : "5",
  }
  // All the subnet IDs, but not available until after planning, so can't be used in foreach. If you need this in
  // foreach, then you need to foreach over subnet_mappings and grab the ids from that.
  all_subnet_ids = [for subnet, _ in local.subnet_mappings : module.ce_network.subnet[subnet].id]
}
