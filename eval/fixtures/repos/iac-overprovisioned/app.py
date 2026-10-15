"""
Synthetic CDK stack with known frugal-architecture violations.

Violations planted for eval-harness test cases:
  Law I  — no cost tags on resources; no billing alerts
  Law IV — no CloudWatch alarms or dashboards
  Law V  — EC2 used where Lambda/Fargate Spot would suffice
  Law VI — RDS provisioned at max capacity from day one; no auto-scaling

Account IDs here are synthetic (123456789012) per SR-EH-001 allow-list.
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from constructs import Construct


class OverprovisionedStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc(self, "Vpc", max_azs=3)

        # Law V violation: always-on r6g.2xlarge for a workload that runs 2h/day
        # Law I violation: no cost-allocation tags
        instance = ec2.Instance(  # noqa: F841
            self,
            "AppServer",
            vpc=vpc,
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.R6G, ec2.InstanceSize.XLARGE2
            ),
            machine_image=ec2.AmazonLinuxImage(),
            # Law IV violation: no CloudWatch detailed monitoring
        )

        # Law VI violation: db.r6g.4xlarge provisioned at max — no auto-scaling plan
        # Law I violation: no cost-allocation tags
        db = rds.DatabaseInstance(  # noqa: F841
            self,
            "AppDatabase",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_15_3
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.R6G, ec2.InstanceSize.XLARGE4
            ),
            vpc=vpc,
            multi_az=True,
            # Law IV violation: no performance insights, no enhanced monitoring
            # Law VI violation: allocated_storage is at maximum with no growth plan
            allocated_storage=16384,
        )
        # No CloudWatch alarms, no billing alerts, no cost tags anywhere — Laws I, IV
