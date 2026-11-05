"""Compiled 4-Zone SQL Bundle Data Model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Compiled4ZoneBundle(BaseModel):
    """Structured output of the 4-Zone DuckDB SQL compiler."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    ir_version: str = "1.0.0"
    topological_load_order: list[str] = Field(default_factory=list)
    zone_0_sql: str
    zone_0_migration_sql: str = ""
    zone_1_sql: str
    zone_2_sql: str
    zone_3_sql: str
    zone_4_query_d10_sql: str
    zone_4_query_d10b_sql: str

    @property
    def zone_4_sql(self) -> str:
        """Combined Zone 4 SQL containing both Query D.10 and Query D.10b."""
        return f"{self.zone_4_query_d10_sql}\n\n{self.zone_4_query_d10b_sql}"

    @property
    def zones_0_to_3_sql(self) -> str:
        """Executable DDL + DML bundle for Zones 0 through 3."""
        sections = [self.zone_0_sql]
        if self.zone_0_migration_sql.strip():
            sections.append(self.zone_0_migration_sql)
        sections.extend(
            [
                self.zone_1_sql,
                self.zone_2_sql,
                self.zone_3_sql,
            ]
        )
        return "\n\n".join(sections)

    @property
    def full_sql_bundle(self) -> str:
        """Complete 4-Zone SQL script (Zones 0-4)."""
        return f"{self.zones_0_to_3_sql}\n\n{self.zone_4_sql}"

    @property
    def full_sql(self) -> str:
        """Alias for `full_sql_bundle`."""
        return self.full_sql_bundle


__all__ = ["Compiled4ZoneBundle"]
