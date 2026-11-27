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

// Phase 5A: Dashboard, Clock State, Announcements, Leave Calendar, Bulk Import
export type WorkMode = components['schemas']['WorkMode']
export type WhoIsInStatus = components['schemas']['WhoIsInStatus']
export type DashboardMemberItemResponse =
  components['schemas']['DashboardMemberItemResponse']
export type WhoIsInGroupResponse = components['schemas']['WhoIsInGroupResponse']
export type UpcomingBirthdayResponse =
  components['schemas']['UpcomingBirthdayResponse']
export type UpcomingHolidayResponse =
  components['schemas']['UpcomingHolidayResponse']
export type QuickReportEntryResponse =
  components['schemas']['QuickReportEntryResponse']
export type DashboardAnnouncementSummaryResponse =
  components['schemas']['DashboardAnnouncementSummaryResponse']
export type AdminDashboardResponse =
  components['schemas']['AdminDashboardResponse']
export type MyDashboardEmployeeSummaryResponse =
  components['schemas']['MyDashboardEmployeeSummaryResponse']
export type EmployeeClockStateResponse =
  components['schemas']['EmployeeClockStateResponse']
export type DashboardApproverResponse =
  components['schemas']['DashboardApproverResponse']
export type DashboardNotificationResponse =
  components['schemas']['DashboardNotificationResponse']
export type MyDashboardResponse = components['schemas']['MyDashboardResponse']

export type AnnouncementCategory =
  components['schemas']['AnnouncementCategory']
export type AnnouncementResponse = components['schemas']['AnnouncementResponse']
export type AnnouncementListResponse =
  components['schemas']['AnnouncementListResponse']
export type AnnouncementAcknowledgementResponse =
  components['schemas']['AnnouncementAcknowledgementResponse']
export type AcknowledgementStatusEmployeeResponse =
  components['schemas']['AcknowledgementStatusEmployeeResponse']
export type AnnouncementAcknowledgementsReportResponse =
  components['schemas']['AnnouncementAcknowledgementsReportResponse']

export type HolidayScope = components['schemas']['HolidayScope']
export type CalendarHolidayResponse =
  components['schemas']['CalendarHolidayResponse']
export type LeaveCalendarResponse = components['schemas']['LeaveCalendarResponse']

export type BulkImportErrorResponse =
  components['schemas']['BulkImportErrorResponse']
export type BulkImportResponse = components['schemas']['BulkImportResponse']

// Phase 5C: Personio Layout + Team Calendar + Clock Segments + Anniversaries
export type TodayClockSegmentResponse =
  components['schemas']['TodayClockSegmentResponse']
export type UpcomingAnniversaryResponse =
  components['schemas']['UpcomingAnniversaryResponse']
export type TeamCalendarEntryResponse =
  components['schemas']['TeamCalendarEntryResponse']
export type TeamCalendarMemberResponse =
  components['schemas']['TeamCalendarMemberResponse']
export type TeamCalendarResponse =
  components['schemas']['TeamCalendarResponse']

// Phase 5E: Self-Selectable Attendance + Two-Stage Leaves + Reports Center
export type RecordManualDayRequest =
  components['schemas']['RecordManualDayRequest']
export type RecordManualDayResponse =
  components['schemas']['RecordManualDayResponse']
export type ExecutiveSummaryResponse =
  components['schemas']['ExecutiveSummaryResponse']
export type StoreReportSummaryResponse =
  components['schemas']['StoreReportSummaryResponse']

// Stage A: Leave Entitlements, Direct Assign, 3-Tier Break Policies
export type BreakMode = components['schemas']['BreakMode']
export type BreakScopeType = components['schemas']['BreakScopeType']
export type BreakTrackingMode = components['schemas']['BreakTrackingMode']
export type BreakWindowSchema = components['schemas']['BreakWindowSchema']
export type BreakPolicyResponse = components['schemas']['BreakPolicyResponse']
export type BreakPolicyListResponse =
  components['schemas']['BreakPolicyListResponse']
export type EffectiveBreakPolicyResponse =
  components['schemas']['EffectiveBreakPolicyResponse']
export type UpsertBreakPolicyRequest =
  components['schemas']['UpsertBreakPolicyRequest']

export type EntitlementAdjustmentType =
  components['schemas']['EntitlementAdjustmentType']
export type LeaveEntitlementAdjustmentResponse =
  components['schemas']['LeaveEntitlementAdjustmentResponse']
export type EmployeeEntitlementResponse =
  components['schemas']['EmployeeEntitlementResponse']
export type LeaveEntitlementListResponse =
  components['schemas']['LeaveEntitlementListResponse']
export type UpsertLeaveEntitlementBody =
  components['schemas']['UpsertLeaveEntitlementBody']
export type BulkUpsertLeaveEntitlementsBody =
  components['schemas']['BulkUpsertLeaveEntitlementsBody']
export type DirectAssignLeaveBody =
  components['schemas']['DirectAssignLeaveBody']

// Phase 5H: Personio Time Management & Attendance Redesign
export type SegmentType = components['schemas']['SegmentType']
export type AttendanceSegmentResponse =
  components['schemas']['AttendanceSegmentResponse']
export type AttendanceDayReviewStatus =
  components['schemas']['AttendanceDayReviewStatus']
export type AttendanceDayReviewResponse =
  components['schemas']['AttendanceDayReviewResponse']
export type BatchReviewAttendanceDaysRequest =
  components['schemas']['BatchReviewAttendanceDaysRequest']
export type BatchReviewAttendanceDaysResponse =
  components['schemas']['BatchReviewAttendanceDaysResponse']

// Phase 5I: Interactive Organization Chart
export type OrgChartNodePublic = components['schemas']['OrgChartNodePublic']
export type OrgChartResponse = components['schemas']['OrgChartResponse']
