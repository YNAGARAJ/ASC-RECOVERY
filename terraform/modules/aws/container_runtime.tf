# ECS on Fargate -- HIPAA-eligible container runtime. Tasks run in the
# private subnets (no public IP); an internet-facing ALB in the public
# subnets is the only ingress path. Health checks target /healthz
# (liveness) at the ALB level, matching src/api/routes/health.py.

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-cluster"
  })
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name_prefix}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role needs to read the secrets injected into the
# container definition below -- distinct from the task role (the app's
# own AWS permissions at runtime), which is deliberately narrower.
resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "${local.name_prefix}-task-execution-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.app.arn,
        aws_secretsmanager_secret.app_database_url.arn,
        aws_secretsmanager_secret.queue_database_url.arn,
      ]
    }]
  })
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# The app's own runtime permissions: read/write the remittances bucket,
# use the KMS key for envelope encryption once a real cloud KMS adapter
# exists (src/security/kms.py) -- deliberately minimal, no wildcard
# resources.
resource "aws_iam_role_policy" "task_app_permissions" {
  name = "${local.name_prefix}-task-app-permissions"
  role = aws_iam_role.task.id

  # tfsec: aws-iam-no-policy-wildcards -- the "/*" suffix is the standard,
  # correct way to grant object-level S3 actions scoped to ONE specific
  # already-named bucket (aws_s3_bucket.remittances.arn), not a true
  # wildcard across resources. There is no way to express "every object
  # in this one bucket" without it.
  #tfsec:ignore:aws-iam-no-policy-wildcards
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.remittances.arn, "${aws_s3_bucket.remittances.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.main.arn]
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.main.arn

  tags = var.tags
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name_prefix}-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.container_cpu)
  memory                   = tostring(var.container_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = var.container_image
      essential = true
      portMappings = [{
        containerPort = 8000
        protocol      = "tcp"
      }]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_secretsmanager_secret_version.app_database_url.arn
        },
        {
          name      = "JWT_SECRET_KEY"
          valueFrom = "${aws_secretsmanager_secret.app.arn}:JWT_SECRET_KEY::"
        },
        {
          name      = "ANTHROPIC_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app.arn}:ANTHROPIC_API_KEY::"
        },
        {
          # F-02 (docs/audit/REGISTER.md): required by src/main.py at
          # startup (EnvKMS reads it) -- without this the container
          # crash-loops before binding :8000.
          name      = "PHI_ENCRYPTION_KEY"
          valueFrom = "${aws_secretsmanager_secret.app.arn}:PHI_ENCRYPTION_KEY::"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "app"
        }
      }
    }
  ])

  tags = var.tags
}

# Phase 7's job-queue worker -- same image as `app`, `entryPoint`
# overridden to run the polling loop (src/worker.py) instead of uvicorn.
# No portMappings/load balancer below: this process never accepts inbound
# traffic, only claims rows from the `jobs` table. Reuses aws_iam_role.task
# and aws_security_group.app as-is (see network.tf's app_to_database rule
# and this file's alb-facing app SG comment) -- the worker's actual
# permission needs (S3 + KMS) are a subset of the app's, so a separate,
# narrower role isn't worth the duplication yet.
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name_prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.container_cpu)
  memory                   = tostring(var.container_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name       = "worker"
      image      = var.container_image
      essential  = true
      entryPoint = ["python", "-m", "worker"]
      secrets = [
        {
          name      = "DATABASE_URL"
          valueFrom = aws_secretsmanager_secret_version.app_database_url.arn
        },
        {
          # BYPASSRLS role, worker-only -- see secrets_and_kms.tf's own
          # comment on why this is never injected into the `app` task.
          name      = "QUEUE_DATABASE_URL"
          valueFrom = aws_secretsmanager_secret_version.queue_database_url.arn
        },
        {
          name      = "PHI_ENCRYPTION_KEY"
          valueFrom = "${aws_secretsmanager_secret.app.arn}:PHI_ENCRYPTION_KEY::"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    }
  ])

  tags = var.tags
}

resource "aws_security_group" "alb" {
  name_prefix = "${local.name_prefix}-alb-"
  vpc_id      = aws_vpc.main.id
  description = "ASC Recovery ALB -- public HTTPS ingress only"

  # tfsec: aws-ec2-no-public-ingress-sgr -- this IS the public ingress
  # path for a SaaS API ambulatory surgery centers reach over the
  # internet; there is no fixed client CIDR to allowlist. Narrowing this
  # would break the product, not harden it.
  #tfsec:ignore:aws-ec2-no-public-ingress-sgr
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # F-07 (docs/audit/REGISTER.md): the environment root's port-80
  # listener (terraform/environments/aws/main.tf) exists only to issue an
  # HTTP->HTTPS redirect -- it never forwards plaintext traffic to a
  # target -- but still needs an inbound path to answer on before it can
  # redirect anything.
  #tfsec:ignore:aws-ec2-no-public-ingress-sgr
  ingress {
    description = "HTTP (redirected to HTTPS by the port-80 listener)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Scoped to the app security group on the app's own port, not
  # 0.0.0.0/0 -- the ALB only ever needs to reach the Fargate tasks
  # behind it, never the open internet.
  egress {
    description     = "To app tasks only"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-alb-sg"
  })
}

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  # tfsec: aws-elb-alb-not-public -- same reason as the ingress rule
  # above: an internal-only ALB would make this a private-network-only
  # API, defeating the point of a public SaaS product.
  #tfsec:ignore:aws-elb-alb-not-public
  internal                  = false
  load_balancer_type        = "application"
  security_groups           = [aws_security_group.alb.id]
  subnets                   = aws_subnet.public[*].id
  drop_invalid_header_fields = true

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-alb"
  })
}

resource "aws_lb_target_group" "app" {
  name        = "${local.name_prefix}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  tags = var.tags
}

# HTTPS listener requires an ACM certificate ARN, which requires a
# domain -- out of scope for this module (supplied by the environment
# root config once a real domain exists). This module exposes the target
# group and ALB (see alb_arn/target_group_arn in outputs.tf); the
# environment root wires the actual aws_lb_listener resources +
# certificate -- see terraform/environments/aws/main.tf (F-07,
# docs/audit/REGISTER.md).

resource "aws_security_group_rule" "alb_to_app" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.app.id
  source_security_group_id = aws_security_group.alb.id
  description              = "ALB to app tasks"
}

resource "aws_ecs_service" "app" {
  name            = "${local.name_prefix}-app"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_lb_target_group.app]

  tags = var.tags
}

# No load_balancer block -- unlike aws_ecs_service.app above, the worker
# has no target group to register with; it is never a request destination.
resource "aws_ecs_service" "worker" {
  name            = "${local.name_prefix}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  tags = var.tags
}
