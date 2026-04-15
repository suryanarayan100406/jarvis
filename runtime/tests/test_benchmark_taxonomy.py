"""Tests for P10-T1 moonshot benchmark taxonomy."""

from __future__ import annotations

from dataclasses import replace
import unittest

from runtime.moonshot import (
    BenchmarkTaxonomyError,
    build_default_benchmark_taxonomy,
    validate_benchmark_taxonomy,
)


class BenchmarkTaxonomyTests(unittest.TestCase):
    def test_default_taxonomy_contains_required_domains(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()

        domain_ids = {domain.domain_id for domain in taxonomy.domains}
        self.assertEqual(domain_ids, {"reasoning", "planning", "memory", "tool_use"})

    def test_default_taxonomy_weights_are_normalized(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()

        self.assertAlmostEqual(sum(domain.weight for domain in taxonomy.domains), 1.0, places=6)

        for domain in taxonomy.domains:
            domain_capabilities = [
                capability
                for capability in taxonomy.capabilities
                if capability.domain_id == domain.domain_id
            ]
            self.assertAlmostEqual(
                sum(capability.weight for capability in domain_capabilities),
                1.0,
                places=6,
            )

    def test_default_taxonomy_has_domain_coverage(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()

        for domain_id in ("reasoning", "planning", "memory", "tool_use"):
            capabilities = taxonomy.list_capabilities(domain_id=domain_id)
            self.assertGreaterEqual(len(capabilities), 3)

    def test_validation_rejects_duplicate_capability_ids(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()
        duplicated = taxonomy.capabilities + (
            replace(taxonomy.capabilities[0], title="Duplicate Capability"),
        )
        invalid = replace(taxonomy, capabilities=duplicated)

        with self.assertRaises(BenchmarkTaxonomyError):
            validate_benchmark_taxonomy(invalid)

    def test_validation_rejects_domain_weight_drift(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()
        modified_domains = []
        for domain in taxonomy.domains:
            if domain.domain_id == "reasoning":
                modified_domains.append(replace(domain, weight=domain.weight + 0.05))
            else:
                modified_domains.append(domain)

        invalid = replace(taxonomy, domains=tuple(modified_domains))

        with self.assertRaises(BenchmarkTaxonomyError):
            validate_benchmark_taxonomy(invalid)

    def test_manifest_output_is_deterministic_and_sorted(self) -> None:
        taxonomy = build_default_benchmark_taxonomy()

        first = taxonomy.to_manifest()
        second = taxonomy.to_manifest()

        self.assertEqual(first, second)

        domain_ids = [domain["domain_id"] for domain in first["domains"]]
        capability_ids = [capability["capability_id"] for capability in first["capabilities"]]

        self.assertEqual(domain_ids, sorted(domain_ids))
        self.assertEqual(capability_ids, sorted(capability_ids))


if __name__ == "__main__":
    unittest.main()
