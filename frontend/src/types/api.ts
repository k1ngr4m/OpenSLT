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
export type WorkflowNode = Schemas['WorkflowNodeOut']
