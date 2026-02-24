from backend.domain.types import LLMProvider
from backend.node.create_nodes.providers import get_default_providers
from .models import LLMProviderDBModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError


class LLMProviderRegistry:
    def __init__(self, session: Session):
        self.session = session

    def reset_defaults(self):
        """Reset all providers to their default values."""
        # Delete all providers
        self.session.query(LLMProviderDBModel).delete()

        # Add default providers
        providers = get_default_providers()
        for provider in providers:
            self.session.add(_to_db_model(provider, is_default=True))

        try:
            self.session.commit()
        except IntegrityError:
            # Another instance already seeded the providers, rollback and continue
            self.session.rollback()

    def update_defaults(self):
        """Update default providers while preserving custom ones."""
        # Get current default providers from DB
        current_defaults = (
            self.session.query(LLMProviderDBModel)
            .filter(LLMProviderDBModel.is_default == True)
            .all()
        )
        current_default_keys = {
            (p.provider, p.model, p.plugin) for p in current_defaults
        }

        # Get new default providers
        new_defaults = get_default_providers()
        new_default_keys = {(p.provider, p.model, p.plugin) for p in new_defaults}

        # Remove old defaults that are no longer in the new defaults
        for provider in current_defaults:
            key = (provider.provider, provider.model, provider.plugin)
            if key not in new_default_keys:
                self.session.delete(provider)

        # Add or update new defaults
        for provider in new_defaults:
            key = (provider.provider, provider.model, provider.plugin)
            if key not in current_default_keys:
                # Add new default provider
                self.session.add(_to_db_model(provider, is_default=True))
            else:
                # Update existing default provider
                self.session.query(LLMProviderDBModel).filter(
                    LLMProviderDBModel.provider == provider.provider,
                    LLMProviderDBModel.model == provider.model,
                    LLMProviderDBModel.plugin == provider.plugin,
                    LLMProviderDBModel.is_default == True,
                ).update(
                    {
                        LLMProviderDBModel.config: provider.config,
                        LLMProviderDBModel.plugin_config: provider.plugin_config,
                    }
                )

        try:
            self.session.commit()
        except IntegrityError:
            # Another instance already seeded the providers, rollback and continue
            self.session.rollback()

    def get_all(self) -> list[LLMProvider]:
        return [
            _to_domain(provider)
            for provider in self.session.query(LLMProviderDBModel).all()
        ]

    async def get_all_dict(self) -> list[dict]:
        providers = self.session.query(LLMProviderDBModel).all()
        result = []

        for provider in providers:
            domain_provider = _to_domain(provider)
            provider_dict = domain_provider.__dict__

            provider_dict["is_available"] = True
            provider_dict["is_model_available"] = True
            provider_dict["is_default"] = provider.is_default

            result.append(provider_dict)

        return result

    def add(self, provider: LLMProvider) -> int:
        model = _to_db_model(provider, is_default=False)
        self.session.add(model)
        self.session.commit()
        return model.id

    def update(self, id: int, provider: LLMProvider):
        self.session.query(LLMProviderDBModel).filter(
            LLMProviderDBModel.id == id
        ).update(
            {
                LLMProviderDBModel.provider: provider.provider,
                LLMProviderDBModel.model: provider.model,
                LLMProviderDBModel.config: provider.config,
                LLMProviderDBModel.plugin: provider.plugin,
                LLMProviderDBModel.plugin_config: provider.plugin_config,
            }
        )
        self.session.commit()

    def delete(self, id: int):
        self.session.query(LLMProviderDBModel).filter(
            LLMProviderDBModel.id == id
        ).delete()
        self.session.commit()


def _to_domain(db_model: LLMProvider | LLMProviderDBModel) -> LLMProvider:
    return LLMProvider(
        id=db_model.id,
        provider=db_model.provider,
        model=db_model.model,
        config=db_model.config,
        plugin=db_model.plugin,
        plugin_config=db_model.plugin_config,
    )


def _to_db_model(domain: LLMProvider, is_default: bool = False) -> LLMProviderDBModel:
    return LLMProviderDBModel(
        provider=domain.provider,
        model=domain.model,
        config=domain.config,
        plugin=domain.plugin,
        plugin_config=domain.plugin_config,
        is_default=is_default,
    )
