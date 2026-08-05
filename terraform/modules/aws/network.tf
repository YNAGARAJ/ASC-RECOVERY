# Network segmentation (Phase 9 prompt: "now explicitly required"):
# private subnets host the database; only public subnets carry a route to
# the internet gateway, and even those exist only to give the NAT
# gateway (which the private-subnet app tasks use for outbound calls to
# the Anthropic API) somewhere to attach. Nothing PHI-bearing ever sits
# in a public subnet.

locals {
  name_prefix        = "${var.project_name}-${var.environment}"
  availability_zones = data.aws_availability_zones.available.names
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-vpc"
  })
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.availability_zones[count.index]

  # Public only in the sense of "has a route to an internet gateway" for
  # the NAT gateway's sake -- no PHI-bearing resource is ever placed here.
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-public-${count.index}"
  })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.availability_zones[count.index]

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-private-${count.index}"
  })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-igw"
  })
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-nat-eip"
  })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-nat"
  })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-public-rt"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-private-rt"
  })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# The Fargate service's own security group -- only this SG (not the
# world) is allowed to reach the database on 5432.
resource "aws_security_group" "app" {
  name_prefix = "${local.name_prefix}-app-"
  vpc_id      = aws_vpc.main.id
  description = "ASC Recovery app tasks"

  # Narrowed to HTTPS only (was all ports/protocols) -- the app only ever
  # makes outbound calls to the Anthropic API and AWS service endpoints
  # (Secrets Manager, KMS, S3, CloudWatch Logs), all HTTPS. The CIDR
  # itself stays 0.0.0.0/0: Anthropic is a third-party SaaS with no fixed,
  # stable IP range to allowlist, so tfsec's public-egress check can't be
  # satisfied by narrowing the CIDR without breaking LLM packet drafting.
  #tfsec:ignore:aws-ec2-no-public-egress-sgr
  egress {
    description = "HTTPS outbound (Anthropic API, AWS API endpoints)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-app-sg"
  })
}

resource "aws_security_group" "database" {
  name_prefix = "${local.name_prefix}-db-"
  vpc_id      = aws_vpc.main.id
  description = "RDS Postgres -- inbound only from the app security group"

  ingress {
    description     = "Postgres from app tasks only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-db-sg"
  })
}

# A separate rule, not an inline block on either security group above --
# an inline egress block on `app` referencing `database.id` and an inline
# ingress block on `database` referencing `app.id` would each need the
# other to exist first, a genuine cycle. Same reason
# container_runtime.tf's `alb_to_app` rule is separate from both the ALB
# and app security groups' own inline blocks.
resource "aws_security_group_rule" "app_to_database" {
  type                     = "egress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.app.id
  source_security_group_id = aws_security_group.database.id
  description               = "App tasks to Postgres"
}
