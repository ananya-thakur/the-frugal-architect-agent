#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { EcrStack } from "../ecr-stack";
import { MonitoringStack } from "../monitoring-stack";

const app = new cdk.App();

const env: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

new EcrStack(app, "FrugalArchitectEcrStack", {
  env,
  description: "ECR repository for the Frugal Architect Agent container image",
});

new MonitoringStack(app, "FrugalArchitectMonitoringStack", {
  env,
  description:
    "CloudWatch dashboard and alarms for the Frugal Architect Agent",
});
