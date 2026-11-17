package kz.qorsa.leadgen.web;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import jakarta.validation.Validator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.service.EgrulLookupService;
import kz.qorsa.leadgen.web.dto.EgrulPatchRequest;
import kz.qorsa.leadgen.web.dto.PendingEgrulCompany;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/**
 * The ЕГРЮЛ boundary (workers/egrul): hands out companies with no known
 * management, and takes back what the register says.
 */
@RestController
@RequestMapping("/api/v1/companies")
public class EgrulController {

    private final EgrulLookupService egrulService;
    private final Validator validator;

    public EgrulController(EgrulLookupService egrulService, Validator validator) {
        this.egrulService = egrulService;
        this.validator = validator;
    }

    /** Companies with no CEO on file and no lookup attempted - hottest first. */
    @GetMapping("/pending-egrul")
    public List<PendingEgrulCompany> pendingEgrul(@RequestParam(defaultValue = "50") int limit) {
        return egrulService.findPending(limit).stream()
                .map(company -> PendingEgrulCompany.builder()
                        .id(company.getId())
                        .name(company.getName())
                        .city(company.getCity())
                        .inn(innFromRaw(company))
                        .build())
                .toList();
    }

    /**
     * Stores one lookup. Always records the attempt, including a lookup that
     * matched nothing. No rescore: a named CEO makes the call easier, not the
     * lead better, so the score is unchanged by definition.
     */
    @PatchMapping("/{id}/egrul")
    public ResponseEntity<Void> patchEgrul(@PathVariable UUID id, @RequestBody EgrulPatchRequest body) {
        Set<ConstraintViolation<EgrulPatchRequest>> violations = validator.validate(body);
        if (!violations.isEmpty()) {
            throw new ConstraintViolationException(violations);
        }
        return egrulService.applyLookup(id, body)
                .map(company -> ResponseEntity.noContent().<Void>build())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Company " + id + " not found"));
    }

    /**
     * ИНН already on file, when a госзакупка notice carried one. The worker
     * prefers looking up by ИНН - it's exact, where a name match is a guess.
     */
    private static String innFromRaw(Company company) {
        if (company.getRaw() == null) {
            return null;
        }
        Object inn = company.getRaw().get("inn");
        return inn == null ? null : String.valueOf(inn);
    }
}
