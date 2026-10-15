import * as cdk from "aws-cdk-lib";
import * as ecr from "aws-cdk-lib/aws-ecr";
import { Construct } from "constructs";

interface EcrStackProps extends cdk.StackProps {
  repositoryName?: string;
}

export class EcrStack extends cdk.Stack {
  public readonly repository: ecr.Repository;

  constructor(scope: Construct, id: string, props: EcrStackProps = {}) {
    super(scope, id, props);

    const repoName = props.repositoryName ?? "frugal-architect-agent";

    this.repository = new ecr.Repository(this, "Repository", {
      repositoryName: repoName,
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.MUTABLE,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Retain last 10 tagged images; expire untagged images after 1 day.
    this.repository.addLifecycleRule({
      tagStatus: ecr.TagStatus.UNTAGGED,
      maxImageAge: cdk.Duration.days(1),
      description: "Expire untagged images after 1 day",
    });
    this.repository.addLifecycleRule({
      tagStatus: ecr.TagStatus.ANY,
      maxImageCount: 10,
      description: "Retain last 10 tagged images",
    });

    new cdk.CfnOutput(this, "RepositoryUri", {
      value: this.repository.repositoryUri,
      description: "ECR repository URI for frugal-architect-agent",
      exportName: `${repoName}-repository-uri`,
    });
  }
}
