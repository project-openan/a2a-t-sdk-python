"""Constants for the built-in content templates (port of Java ``StandardTemplates``).

Each template is published in two spellings (D16/D17, "双常量表"): a typed
:class:`~a2a_t.core.template_uri.TemplateUri` and its raw :attr:`~a2a_t.core.template_uri.TemplateUri.uri`
string (constant name suffixed ``_URI``). The typed form is the identity
representation used for template comparisons and by internal seams; the string
form is what the ``A2ATClient``/``A2ATServer`` facades take as their
``template_uri`` parameter. Use these constants instead of hand-written URI
strings to keep the spelling centralized — a lockstep consistency test pins the
two forms together and verifies the template files exist on disk for both
languages.

Example: ``StandardTemplates.ENERGY_SAVING_URI`` is
``Task-T/network-layer/ran-energy-saving/v1``. The Task-T and Notification-T
templates carry the ``network-layer`` domain segment; Authorization-T and
Negotiation-T templates do not.
"""

from __future__ import annotations

from typing import Final

from a2a_t.core.template_uri import TemplateUri

__all__ = [
    "AUTHORIZATION",
    "AUTHORIZATION_EXTENSION_NAME",
    "AUTHORIZATION_POLICY_MANAGEMENT",
    "AUTHORIZATION_POLICY_MANAGEMENT_URI",
    "AUTHORIZATION_URIS",
    "ENERGY_SAVING",
    "ENERGY_SAVING_URI",
    "FEASIBILITY_NEGOTIATION_ACCEPT_REJECT",
    "FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI",
    "FEASIBILITY_NEGOTIATION_PROPOSE",
    "FEASIBILITY_NEGOTIATION_PROPOSE_URI",
    "INFORMATION_NEGOTIATION_ACCEPT_REJECT",
    "INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI",
    "INFORMATION_NEGOTIATION_PROPOSE",
    "INFORMATION_NEGOTIATION_PROPOSE_URI",
    "NEGOTIATION",
    "NEGOTIATION_ABORT",
    "NEGOTIATION_ABORT_URI",
    "NEGOTIATION_EXTENSION_NAME",
    "NEGOTIATION_URIS",
    "NETWORK_LAYER_SEGMENT",
    "NOTIFICATION",
    "NOTIFICATION_EXTENSION_NAME",
    "NOTIFICATION_URIS",
    "PRIVATE_LINE_COMPLAINT",
    "PRIVATE_LINE_COMPLAINT_URI",
    "SERVICE_RECOVERY",
    "SERVICE_RECOVERY_URI",
    "SUBSCRIBE_INCIDENT",
    "SUBSCRIBE_INCIDENT_URI",
    "TASK",
    "TASK_EXTENSION_NAME",
    "TASK_URIS",
    "TARGET_NEGOTIATION_ACCEPT_REJECT",
    "TARGET_NEGOTIATION_ACCEPT_REJECT_URI",
    "TARGET_NEGOTIATION_PROPOSE",
    "TARGET_NEGOTIATION_PROPOSE_URI",
    "RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE",
    "RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE_URI",
]

#: Extension name of the Task-T template family.
TASK_EXTENSION_NAME: Final[str] = "Task-T"

#: Extension name of the Notification-T template family.
NOTIFICATION_EXTENSION_NAME: Final[str] = "Notification-T"

#: Extension name of the Authorization-T template family.
AUTHORIZATION_EXTENSION_NAME: Final[str] = "Authorization-T"

#: Extension name of the Negotiation-T template family.
NEGOTIATION_EXTENSION_NAME: Final[str] = "Negotiation-T"

#: Domain segment carried by the Task-T and Notification-T template paths.
NETWORK_LAYER_SEGMENT: Final[str] = "network-layer"

#: Task-T template for the ran-energy-saving scenario.
ENERGY_SAVING: Final[TemplateUri] = TemplateUri.of(TASK_EXTENSION_NAME, NETWORK_LAYER_SEGMENT, "ran-energy-saving")

#: Raw template URI of ``ENERGY_SAVING``: ``Task-T/network-layer/ran-energy-saving/v1``.
ENERGY_SAVING_URI: Final[str] = ENERGY_SAVING.uri

#: Task-T template for the private-line-complaint scenario.
PRIVATE_LINE_COMPLAINT: Final[TemplateUri] = TemplateUri.of(
    TASK_EXTENSION_NAME, NETWORK_LAYER_SEGMENT, "private-line-complaint"
)

#: Raw template URI of ``PRIVATE_LINE_COMPLAINT``: ``Task-T/network-layer/private-line-complaint/v1``.
PRIVATE_LINE_COMPLAINT_URI: Final[str] = PRIVATE_LINE_COMPLAINT.uri

#: Task-T template for the ran-intelligent-backup-battery-endurance scenario.
RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE: Final[TemplateUri] = TemplateUri.of(
    TASK_EXTENSION_NAME, NETWORK_LAYER_SEGMENT, "ran-intelligent-backup-battery-endurance"
)

#: Raw template URI of ``RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE``:
#: ``Task-T/network-layer/ran-intelligent-backup-battery-endurance/v1``.
RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE_URI: Final[str] = RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE.uri

#: Notification-T template for the subscribe-incident scenario.
SUBSCRIBE_INCIDENT: Final[TemplateUri] = TemplateUri.of(
    NOTIFICATION_EXTENSION_NAME, NETWORK_LAYER_SEGMENT, "subscribe-incident"
)

#: Raw template URI of ``SUBSCRIBE_INCIDENT``: ``Notification-T/network-layer/subscribe-incident/v1``.
SUBSCRIBE_INCIDENT_URI: Final[str] = SUBSCRIBE_INCIDENT.uri

#: Notification-T template for the service-recovery scenario.
SERVICE_RECOVERY: Final[TemplateUri] = TemplateUri.of(
    NOTIFICATION_EXTENSION_NAME, NETWORK_LAYER_SEGMENT, "service-recovery"
)

#: Raw template URI of ``SERVICE_RECOVERY``: ``Notification-T/network-layer/service-recovery/v1``.
SERVICE_RECOVERY_URI: Final[str] = SERVICE_RECOVERY.uri

#: Authorization-T template for the authorization-policy-management scenario.
AUTHORIZATION_POLICY_MANAGEMENT: Final[TemplateUri] = TemplateUri.of(
    AUTHORIZATION_EXTENSION_NAME, "authorization-policy-management"
)

#: Raw template URI of ``AUTHORIZATION_POLICY_MANAGEMENT``:
#: ``Authorization-T/authorization-policy-management/v1``.
AUTHORIZATION_POLICY_MANAGEMENT_URI: Final[str] = AUTHORIZATION_POLICY_MANAGEMENT.uri

#: Negotiation-T propose template for information negotiation.
INFORMATION_NEGOTIATION_PROPOSE: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "information-negotiation", "propose"
)

#: Raw template URI of ``INFORMATION_NEGOTIATION_PROPOSE``:
#: ``Negotiation-T/information-negotiation/propose/v1``.
INFORMATION_NEGOTIATION_PROPOSE_URI: Final[str] = INFORMATION_NEGOTIATION_PROPOSE.uri

#: Negotiation-T accept-reject template for information negotiation.
INFORMATION_NEGOTIATION_ACCEPT_REJECT: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "information-negotiation", "accept-reject"
)

#: Raw template URI of ``INFORMATION_NEGOTIATION_ACCEPT_REJECT``:
#: ``Negotiation-T/information-negotiation/accept-reject/v1``.
INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI: Final[str] = INFORMATION_NEGOTIATION_ACCEPT_REJECT.uri

#: Negotiation-T propose template for target negotiation.
TARGET_NEGOTIATION_PROPOSE: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "target-negotiation", "propose"
)

#: Raw template URI of ``TARGET_NEGOTIATION_PROPOSE``:
#: ``Negotiation-T/target-negotiation/propose/v1``.
TARGET_NEGOTIATION_PROPOSE_URI: Final[str] = TARGET_NEGOTIATION_PROPOSE.uri

#: Negotiation-T accept-reject template for target negotiation.
TARGET_NEGOTIATION_ACCEPT_REJECT: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "target-negotiation", "accept-reject"
)

#: Raw template URI of ``TARGET_NEGOTIATION_ACCEPT_REJECT``:
#: ``Negotiation-T/target-negotiation/accept-reject/v1``.
TARGET_NEGOTIATION_ACCEPT_REJECT_URI: Final[str] = TARGET_NEGOTIATION_ACCEPT_REJECT.uri

#: Negotiation-T propose template for feasibility negotiation.
FEASIBILITY_NEGOTIATION_PROPOSE: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "feasibility-negotiation", "propose"
)

#: Raw template URI of ``FEASIBILITY_NEGOTIATION_PROPOSE``:
#: ``Negotiation-T/feasibility-negotiation/propose/v1``.
FEASIBILITY_NEGOTIATION_PROPOSE_URI: Final[str] = FEASIBILITY_NEGOTIATION_PROPOSE.uri

#: Negotiation-T accept-reject template for feasibility negotiation.
FEASIBILITY_NEGOTIATION_ACCEPT_REJECT: Final[TemplateUri] = TemplateUri.of(
    NEGOTIATION_EXTENSION_NAME, "feasibility-negotiation", "accept-reject"
)

#: Raw template URI of ``FEASIBILITY_NEGOTIATION_ACCEPT_REJECT``:
#: ``Negotiation-T/feasibility-negotiation/accept-reject/v1``.
FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI: Final[str] = FEASIBILITY_NEGOTIATION_ACCEPT_REJECT.uri

#: Negotiation-T common abort template.
NEGOTIATION_ABORT: Final[TemplateUri] = TemplateUri.of(NEGOTIATION_EXTENSION_NAME, "common", "abort")

#: Raw template URI of ``NEGOTIATION_ABORT``: ``Negotiation-T/common/abort/v1``.
NEGOTIATION_ABORT_URI: Final[str] = NEGOTIATION_ABORT.uri

#: All built-in Task-T templates.
TASK: Final[tuple[TemplateUri, ...]] = (
    ENERGY_SAVING,
    PRIVATE_LINE_COMPLAINT,
    RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE,
)

#: Raw template URIs of all built-in Task-T templates.
TASK_URIS: Final[tuple[str, ...]] = (
    ENERGY_SAVING_URI,
    PRIVATE_LINE_COMPLAINT_URI,
    RAN_INTELLIGENT_BACKUP_BATTERY_ENDURANCE_URI,
)

#: All built-in Notification-T templates.
NOTIFICATION: Final[tuple[TemplateUri, ...]] = (SUBSCRIBE_INCIDENT, SERVICE_RECOVERY)

#: Raw template URIs of all built-in Notification-T templates.
NOTIFICATION_URIS: Final[tuple[str, ...]] = (SUBSCRIBE_INCIDENT_URI, SERVICE_RECOVERY_URI)

#: All built-in Authorization-T templates.
AUTHORIZATION: Final[tuple[TemplateUri, ...]] = (AUTHORIZATION_POLICY_MANAGEMENT,)

#: Raw template URIs of all built-in Authorization-T templates.
AUTHORIZATION_URIS: Final[tuple[str, ...]] = (AUTHORIZATION_POLICY_MANAGEMENT_URI,)

#: All built-in Negotiation-T templates.
NEGOTIATION: Final[tuple[TemplateUri, ...]] = (
    INFORMATION_NEGOTIATION_PROPOSE,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT,
    TARGET_NEGOTIATION_PROPOSE,
    TARGET_NEGOTIATION_ACCEPT_REJECT,
    FEASIBILITY_NEGOTIATION_PROPOSE,
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT,
    NEGOTIATION_ABORT,
)

#: Raw template URIs of all built-in Negotiation-T templates.
NEGOTIATION_URIS: Final[tuple[str, ...]] = (
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    NEGOTIATION_ABORT_URI,
)
