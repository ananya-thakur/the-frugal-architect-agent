import * as cdk from "aws-cdk-lib";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

interface EvalHarnessStackProps extends cdk.StackProps {
  agentcoreEndpoint: string;
  agentName?: string;
  reportRetentionDays?: number;
  otlpEndpoint?: string;
}

export class EvalHarnessStack extends cdk.Stack {
  public readonly evalProject: codebuild.Project;
  public readonly reportBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: EvalHarnessStackProps) {
    super(scope, id, props);

    const agentName = props.agentName ?? "frugal-architect-agent";
    const retentionDays = props.reportRetentionDays ?? 90;

    // ── Report archive bucket ────────────────────────────────────────────────
    this.reportBucket = new s3.Bucket(this, "EvalReportBucket", {
      bucketName: `${agentName}-eval-reports-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: "expire-eval-reports",
          expiration: cdk.Duration.days(retentionDays),
          enabled: true,
        },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ── IAM execution role for eval CodeBuild ────────────────────────────────
    // Least-privilege role; explicitly denied cost/model APIs per security-design-eval-harness.
    const evalRole = new iam.Role(this, "EvalCiRole", {
      roleName: `${agentName}-eval-ci-role`,
      assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
      description:
        "Least-privilege eval CI role; no direct Bedrock/cost API access",
    });

    evalRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreInvoke",
        actions: ["bedrock-agentcore:InvokeAgent"],
        resources: [
          `arn:aws:bedrock-agentcore:*:${this.account}:agent/${agentName}*`,
        ],
      })
    );

    evalRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "CloudWatchEvalMetrics",
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"],
        conditions: {
          StringEquals: {
            "cloudwatch:namespace": `${agentName}/eval`,
          },
        },
      })
    );

    evalRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "EvalReportUpload",
        actions: ["s3:PutObject"],
        resources: [`${this.reportBucket.bucketArn}/reports/*`],
      })
    );

    // Advisory-only: deny cost/account APIs and direct model invocations.
    evalRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "DenyAdvisoryOnlyCostAndModelAPIs",
        effect: iam.Effect.DENY,
        actions: [
          "ce:*",
          "budgets:*",
          "compute-optimizer:*",
          "pricing:*",
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: ["*"],
      })
    );

    // ── Build environment variables ──────────────────────────────────────────
    const envVars: Record<string, codebuild.BuildEnvironmentVariable> = {
      AGENTCORE_ENDPOINT: {
        value: props.agentcoreEndpoint,
        type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
      },
      REPORT_BUCKET: {
        value: this.reportBucket.bucketName,
        type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
      },
    };

    if (props.otlpEndpoint) {
      envVars["OTLP_ENDPOINT"] = {
        value: props.otlpEndpoint,
        type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
      };
    }

    // ── CodeBuild project ────────────────────────────────────────────────────
    this.evalProject = new codebuild.Project(this, "EvalProject", {
      projectName: `${agentName}-eval`,
      description: "Offline quality evaluation harness for frugal-architect-agent",
      role: evalRole,
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.SMALL,
        privileged: false,
      },
      buildSpec: codebuild.BuildSpec.fromSourceFilename("buildspec-eval.yml"),
      environmentVariables: envVars,
      artifacts: codebuild.Artifacts.s3({
        bucket: this.reportBucket,
        path: "reports",
        includeBuildId: true,
        packageZip: false,
      }),
      timeout: cdk.Duration.minutes(30),
      concurrentBuildLimit: 1,
      logging: {
        cloudWatch: {
          logGroup: new (require("aws-cdk-lib/aws-logs").LogGroup)(
            this,
            "EvalLogGroup",
            {
              logGroupName: `/aws/codebuild/${agentName}-eval`,
              retention: require("aws-cdk-lib/aws-logs").RetentionDays.ONE_MONTH,
              removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
          ),
        },
      },
    });

    // ── Outputs ───────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "EvalProjectName", {
      value: this.evalProject.projectName,
      description: "CodeBuild project name for eval-harness",
      exportName: `${agentName}-eval-project-name`,
    });

    new cdk.CfnOutput(this, "EvalReportBucketName", {
      value: this.reportBucket.bucketName,
      description: "S3 bucket for eval reports (90-day retention)",
      exportName: `${agentName}-eval-report-bucket`,
    });
  }
}
