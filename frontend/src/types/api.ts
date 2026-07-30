import type { components } from '@/types/api.generated'

type Schemas = components['schemas']

export type ApiAuditLog = Schemas['AuditOut']
export type ApiLog = Schemas['LogOut']
export type ApiPlan = Schemas['PlanOut']
export type ApiResource = Schemas['ResourceOut']
export type ApiScenario = Schemas['ScenarioOut']
export type ApiUser = Schemas['UserOut']
export type ApiUserCreate = Schemas['UserCreate']
export type ApiUserUpdate = Schemas['UserUpdate']
export type WorkflowDocument = Schemas['WorkflowDocumentOut']
export type WorkflowNode = NonNullable<Schemas['WorkflowVersionOut']['nodes']>[number]
export type WorkflowNodeType = WorkflowNode['node_type']
export type WorkflowNodeConfig = Partial<
  Schemas['ServerConfig'] &
  Schemas['DatabaseConfig'] &
  Schemas['WiringConfirmationConfig'] &
  Schemas['RemStartupConfig'] &
  Schemas['SlnicStartConfig'] &
  Schemas['SlnicStopConfig'] &
  Schemas['SlnicMergeConfig'] &
  Schemas['MarketStartupConfig'] &
  Schemas['OrderPreparationConfig'] &
  Schemas['ParserConfig'] &
  Schemas['StatisticsConfig'] &
  Schemas['ReportGenerationConfig']
>
export type EditableWorkflowNode = {
  id?: number
  node_key: string
  position: number
  node_type: WorkflowNodeType
  name: string
  config: WorkflowNodeConfig
}
