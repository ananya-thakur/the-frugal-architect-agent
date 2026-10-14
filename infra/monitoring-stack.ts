import * as cdk from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import { Construct } from "constructs";

interface MonitoringStackProps extends cdk.StackProps {
  agentName?: string;
  alarmEmail?: string;
}

export class MonitoringStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: MonitoringStackProps = {}) {
    super(scope, id, props);

    const agentName = props.agentName ?? "frugal-architect-agent";

    // ── Metrics ────────────────────────────────────────────────────────────────
    const invocationLatency = new cloudwatch.Metric({
      namespace: agentName,
      metricName: "invocation.duration_ms",
      statistic: "p99",
      period: cdk.Duration.minutes(5),
      label: "Invocation latency p99",
    });

    const tokenUsage = new cloudwatch.Metric({
      namespace: agentName,
      metricName: "model.tokens_total",
      statistic: "Sum",
      period: cdk.Duration.minutes(5),
      label: "Total tokens (input + output)",
    });

    const costPerRun = new cloudwatch.Metric({
      namespace: agentName,
      metricName: "model.cost_usd",
      statistic: "Sum",
      period: cdk.Duration.minutes(5),
      label: "Estimated cost per run (USD)",
    });

    const errorCount = new cloudwatch.Metric({
      namespace: agentName,
      metricName: "invocation.errors",
      statistic: "Sum",
      period: cdk.Duration.minutes(5),
      label: "Invocation errors",
    });

    // ── Alarms ─────────────────────────────────────────────────────────────────
    new cloudwatch.Alarm(this, "HighLatencyAlarm", {
      alarmName: `${agentName}-high-latency`,
      metric: invocationLatency,
      threshold: 30_000, // 30 seconds p99
      evaluationPeriods: 3,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription:
        "Invocation p99 latency exceeded 30 s — investigate model or tool latency",
    });

    new cloudwatch.Alarm(this, "ErrorRateAlarm", {
      alarmName: `${agentName}-error-rate`,
      metric: errorCount,
      threshold: 5,
      evaluationPeriods: 2,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: "More than 5 invocation errors in 10 minutes",
    });

    // ── Dashboard ─────────────────────────────────────────────────────────────
    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: agentName,
      widgets: [
        [
          new cloudwatch.GraphWidget({
            title: "Invocation Latency (p99)",
            left: [invocationLatency],
            width: 12,
          }),
          new cloudwatch.GraphWidget({
            title: "Errors",
            left: [errorCount],
            width: 12,
          }),
        ],
        [
          new cloudwatch.GraphWidget({
            title: "Token Usage",
            left: [tokenUsage],
            width: 12,
          }),
          new cloudwatch.GraphWidget({
            title: "Estimated Cost (USD)",
            left: [costPerRun],
            width: 12,
          }),
        ],
      ],
    });
  }
}
