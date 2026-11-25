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

export type EmployeeResponse = components['schemas']['EmployeeResponse']
export type EmployeeCreateRequest =
  components['schemas']['EmployeeCreateRequest']
export type EmployeeUpdateRequest =
  components['schemas']['EmployeeUpdateRequest']
export type EmployeeSensitivePiiResponse =
  components['schemas']['EmployeeSensitivePiiResponse']
export type ContractType = components['schemas']['ContractType']
export type WorkScheduleType = components['schemas']['WorkScheduleType']
export type EmploymentStatus = components['schemas']['EmploymentStatus']

export type AlertCode = components['schemas']['AlertCode']
export type AlertSeverity = components['schemas']['AlertSeverity']
export type AppendCorrectionRequest =
  components['schemas']['AppendCorrectionRequest']
export type AttendanceEventResponse =
  components['schemas']['AttendanceEventResponse']
export type AttendanceEventType = components['schemas']['AttendanceEventType']
export type AttendanceLedgerResponse =
  components['schemas']['AttendanceLedgerResponse']
export type AttendanceSource = components['schemas']['AttendanceSource']
export type ClockAttendanceRequest =
  components['schemas']['ClockAttendanceRequest']
export type ClockAttendanceResponse =
  components['schemas']['ClockAttendanceResponse']
export type ComplianceAlertResponse =
  components['schemas']['ComplianceAlertResponse']
export type CorrectedEventType = components['schemas']['CorrectedEventType']
export type DailyAttendanceSummaryResponse =
  components['schemas']['DailyAttendanceSummaryResponse']
export type EmployeeAttendanceLedgerResponse =
  components['schemas']['EmployeeAttendanceLedgerResponse']
export type WorkSessionResponse = components['schemas']['WorkSessionResponse']

// Phase 4: Scheduling
export type ShiftStatus = components['schemas']['ShiftStatus']
export type ShiftResponse = components['schemas']['ShiftResponse']
export type ShiftMutationResponse =
  components['schemas']['ShiftMutationResponse']
export type CreateShiftRequest = components['schemas']['CreateShiftRequest']
export type UpdateShiftRequest = components['schemas']['UpdateShiftRequest']
export type PublishWeekRequest = components['schemas']['PublishWeekRequest']
export type PublishWeekResponse = components['schemas']['PublishWeekResponse']
export type ScheduleAlertCode = components['schemas']['ScheduleAlertCode']
export type ScheduleAlertResponse =
  components['schemas']['ScheduleAlertResponse']
export type EmployeeScheduleRowResponse =
  components['schemas']['EmployeeScheduleRowResponse']
export type WeeklyScheduleResponse =
  components['schemas']['WeeklyScheduleResponse']
export type ApprovedLeaveSummaryResponse =
  components['schemas']['ApprovedLeaveSummaryResponse']

// Phase 4: Leaves
export type LeaveType = components['schemas']['LeaveType']
export type LeaveStatus = components['schemas']['LeaveStatus']
export type LeaveRequestResponse = components['schemas']['LeaveRequestResponse']
export type LeaveListResponse = components['schemas']['LeaveListResponse']
export type CreateLeaveRequestBody =
  components['schemas']['CreateLeaveRequestBody']
export type ReviewLeaveRequestBody =
  components['schemas']['ReviewLeaveRequestBody']
export type VacationBalanceResponse =
  components['schemas']['VacationBalanceResponse']

// Phase 4: Compliance Documents & SHA-256 Receipts
export type DocumentCategory = components['schemas']['DocumentCategory']
export type StorageBackend = components['schemas']['StorageBackend']
export type ComplianceDocumentResponse =
  components['schemas']['ComplianceDocumentResponse']
export type DocumentReceiptResponse =
  components['schemas']['DocumentReceiptResponse']
export type DocumentListItemResponse =
  components['schemas']['DocumentListItemResponse']
export type DocumentListResponse = components['schemas']['DocumentListResponse']
export type DocumentDetailResponse =
  components['schemas']['DocumentDetailResponse']
export type CreateSignedUrlRequest =
  components['schemas']['CreateSignedUrlRequest']
export type SignedUrlResponse = components['schemas']['SignedUrlResponse']

// Phase 5: Monthly Payroll Closing & Gestoría Export
export type MonthlyClosingStatus = components['schemas']['MonthlyClosingStatus']
export type ClosingBlockerCode = components['schemas']['ClosingBlockerCode']
export type ClosingBlockerResponse =
  components['schemas']['ClosingBlockerResponse']
export type MonthlyClosingResponse =
  components['schemas']['MonthlyClosingResponse']
export type MonthlyClosingItemResponse =
  components['schemas']['MonthlyClosingItemResponse']
export type MonthlyClosingDetailResponse =
  components['schemas']['MonthlyClosingDetailResponse']
export type MonthlyClosingListItemResponse =
  components['schemas']['MonthlyClosingListItemResponse']
export type MonthlyClosingListResponse =
  components['schemas']['MonthlyClosingListResponse']
export type MonthlyEmployeePreviewResponse =
  components['schemas']['MonthlyEmployeePreviewResponse']
export type MonthlyReadinessPreviewResponse =
  components['schemas']['MonthlyReadinessPreviewResponse']
export type CloseMonthlyPayrollRequest =
  components['schemas']['CloseMonthlyPayrollRequest']
export type ReopenMonthlyClosingRequest =
  components['schemas']['ReopenMonthlyClosingRequest']
