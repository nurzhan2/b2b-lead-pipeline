package kz.qorsa.leadgen.web;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.service.OutreachExportService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The outbound boundary: which HOT leads still need pushing to a CRM, and
 * confirmation of the ones that made it.
 */
@RestController
@RequestMapping("/api/v1/outreach")
@Validated
public class OutreachController {

    private final OutreachExportService exportService;

    public OutreachController(OutreachExportService exportService) {
        this.exportService = exportService;
    }

    /** HOT leads not yet in the given channel, hottest first. */
    @GetMapping("/pending")
    public List<PendingLead> pending(
            @RequestParam(defaultValue = OutreachExportService.CHANNEL_AMOCRM) String channel,
            @RequestParam(defaultValue = "50") int limit) {
        return exportService.findPending(channel, limit).stream()
                .map(OutreachController::toPending)
                .toList();
    }

    /** Records a lead as pushed. Idempotent - a retry after a lost response is normal. */
    @PostMapping("/{leadId}/pushed")
    public ResponseEntity<Void> markPushed(@PathVariable UUID leadId, @RequestBody PushedRequest body) {
        return exportService.markPushed(leadId, body.channel(), body.externalId())
                .map(o -> ResponseEntity.noContent().<Void>build())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Lead " + leadId + " not found"));
    }

    private static PendingLead toPending(Lead lead) {
        Company company = lead.getCompany();
        return new PendingLead(
                lead.getId(),
                company.getName(),
                lead.getScore(),
                lead.getHotReason(),
                company.getPhone(),
                company.getEmail(),
                company.getMessenger(),
                company.getCity(),
                company.getDomain(),
                company.getSource().name(),
                company.getSourceUrl(),
                rawString(company, "contact_person"),
                rawString(company, "ceo_name"),
                rawString(company, "ceo_post"),
                rawString(company, "inn"),
                rawString(company, "audit_failed_checks"));
    }

    private static String rawString(Company company, String key) {
        if (company.getRaw() == null) {
            return null;
        }
        Object value = company.getRaw().get(key);
        return value == null ? null : String.valueOf(value);
    }

    /** Everything workers/amocrm needs to build a deal, a contact and a note. */
    public record PendingLead(
            UUID leadId,
            String name,
            int score,
            String hotReason,
            String phone,
            String email,
            String messenger,
            String city,
            String domain,
            String source,
            String sourceUrl,
            String contactPerson,
            String ceoName,
            String ceoPost,
            String inn,
            String auditFailedChecks) {
    }

    public record PushedRequest(
            @NotBlank @Size(max = 50) String channel,
            @NotBlank @Size(max = 100) String externalId) {
    }
}
