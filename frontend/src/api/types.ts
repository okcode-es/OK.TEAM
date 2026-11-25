import type { components } from '@/api/generated/schema'

/**
 * Strictly derived from contracts/openapi.json via openapi-typescript.
 * NEVER hand-write duplicate backend DTO interfaces.
 */
export type AuthMeResponse = components['schemas']['AuthMeResponse']
export type TenantMembershipSummary =
  components['schemas']['TenantMembershipSummaryResponse']
export type TenantResponse = components['schemas']['TenantResponse']
export type TenantCreateRequest = components['schemas']['TenantCreateRequest']
export type TenantUpdateRequest = components['schemas']['TenantUpdateRequest']
export type StoreResponse = components['schemas']['StoreResponse']
export type StoreCreateRequest = components['schemas']['StoreCreateRequest']
export type StoreUpdateRequest = components['schemas']['StoreUpdateRequest']
export type MembershipResponse = components['schemas']['MembershipResponse']
export type MembershipCreateRequest =
  components['schemas']['MembershipCreateRequest']
export type MembershipUpdateRequest =
  components['schemas']['MembershipUpdateRequest']
export type UserRole = components['schemas']['UserRole']
export type MembershipStatus = components['schemas']['MembershipStatus']
