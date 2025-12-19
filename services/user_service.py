"""
User Service
Version: 10.0

User identity management.
DEPENDS ON: api_gateway.py, cache_service.py, models.py, config.py
"""

import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from models import UserMapping
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class UserService:
    """
    User identity management.
    
    Handles:
    - User lookup by phone
    - Auto-onboarding from API
    - Context building
    """
    
    def __init__(
        self,
        db: AsyncSession,
        gateway=None,
        cache=None
    ):
        """
        Initialize user service.
        
        Args:
            db: Database session
            gateway: API Gateway (optional)
            cache: Cache service (optional)
        """
        self.db = db
        self.gateway = gateway
        self.cache = cache
        self.tenant_id = settings.tenant_id

    async def get_active_identity(self, phone: str) -> Optional['UserMapping']:
        """
        Get user from database, trying multiple phone formats.
        
        Args:
            phone: Phone number
            
        Returns:
            UserMapping or None
        """
        try:
            # Generate possible phone number variations
            variations = set([phone])
            
            digits_only = "".join(filter(str.isdigit, phone))
            if digits_only != phone:
                variations.add(digits_only)

            # From +385... to 385...
            if phone.startswith("+"):
                variations.add(phone[1:])

            # From 385... to +385...
            if phone.startswith("385"):
                variations.add("+" + phone)
            
            # From 385... to 0...
            if digits_only.startswith("385") and len(digits_only) > 3:
                variations.add("0" + digits_only[3:])
            
            # From 0... to 385...
            if digits_only.startswith("0") and len(digits_only) > 1:
                variations.add("385" + digits_only[1:])

            logger.debug(f"Attempting user lookup with phone variations: {variations}")

            stmt = select(UserMapping).where(
                UserMapping.phone_number.in_(list(variations)),
                UserMapping.is_active == True
            ).limit(1)

            result = await self.db.execute(stmt)
            user = result.scalars().first()
            
            if user:
                logger.info(f"Found active user '{user.display_name}' for phone '{phone}' using variation '{user.phone_number}'")
            else:
                logger.warning(f"No active user found for phone '{phone}' with variations {variations}")
                
            return user
        except Exception as e:
            logger.error(f"DB lookup failed for phone '{phone}': {e}")
            return None
    
    # ... (rest of class)
    
    async def try_auto_onboard(self, phone: str) -> Optional[Tuple[str, str]]:
        """Pokušaj onboardanja koristeći isključivo 'Phone' polje s varijacijama."""
        if not self.gateway:
            return None
        
        try:
            # Čistimo broj na dva načina (međunarodni i lokalni)
            # 1. 38595... (bez plusa)
            clean_intl = phone.replace("+", "").strip().replace(" ", "")
            # 2. 095... (ako je slučajno tako spremljeno u bazi)
            clean_local = clean_intl
            if clean_intl.startswith("385"):
                clean_local = "0" + clean_intl[3:]

            variations = [clean_intl, clean_local]
            from services.api_gateway import HttpMethod

            for phone_var in variations:
                logger.info(f"Tražim korisnika po broju: {phone_var}")
                
                # Koristimo točan format koji je potvrdio curl i log
                filter_str = f"Phone%28=%29{phone_var}"
                
                response = await self.gateway.execute(
                    method=HttpMethod.GET,
                    path="/tenantmgt/Persons",
                    params={"Filter": filter_str}
                )

                if response.success:
                    data = response.data
                    items = data if isinstance(data, list) else data.get("Items", [])
                    
                    if items:
                        person = items[0]
                        person_id = person.get("Id")
                        display_name = person.get("DisplayName", "Korisnik")
                        
                        logger.info(f"✅ Korisnik pronađen: {display_name}")
                        
                        # Spremanje u bazu
                        vehicle_info = await self._get_vehicle_info(person_id)
                        await self._upsert_mapping(phone, person_id, display_name)
                        return (display_name, vehicle_info)

            logger.warning(f"❌ Korisnik nije pronađen na API-ju niti s jednom varijacijom: {variations}")
            return None
            
        except Exception as e:
            logger.error(f"Auto-onboard failed: {e}")
            return None
    
    def _extract_name(self, person: Dict) -> str:
        """Extract display name from person data."""
        name = (
            person.get("DisplayName") or
            f"{person.get('FirstName', '')} {person.get('LastName', '')}".strip() or
            "Korisnik"
        )
        
        # Clean "A-1 - Surname, Name" format
        if " - " in name:
            parts = name.split(" - ")
            if len(parts) > 1:
                name_part = parts[-1].strip()
                if ", " in name_part:
                    surname, firstname = name_part.split(", ", 1)
                    name = f"{firstname} {surname}"
                else:
                    name = name_part
        
        return name
    
    async def _get_vehicle_info(self, person_id: str) -> str:
        """Get vehicle description for person."""
        try:
            from services.api_gateway import HttpMethod
            
            response = await self.gateway.execute(
                method=HttpMethod.GET,
                path="/automation/MasterData",
                params={"personId": person_id}
            )
            
            if not response.success:
                return "Nepoznato"
            
            data = response.data
            
            if isinstance(data, list):
                data = data[0] if data else {}
            elif isinstance(data, dict) and "Data" in data:
                items = data["Data"]
                data = items[0] if items else {}
            
            plate = data.get("LicencePlate") or data.get("Plate")
            name = data.get("FullVehicleName") or data.get("DisplayName")
            
            if plate:
                return f"{name or 'Vozilo'} ({plate})"
            return name or "Nema dodijeljenog vozila"
            
        except Exception:
            return "Nepoznato"
    
    async def _save_mapping(self, phone: str, person_id: str, name: str) -> None:
        """Save user mapping to database."""
        try:
            stmt = pg_insert(UserMapping).values(
                phone_number=phone,
                api_identity=person_id,
                display_name=name,
                is_active=True,
                updated_at=datetime.utcnow()
            ).on_conflict_do_update(
                index_elements=['phone_number'],
                set_={
                    'api_identity': person_id,
                    'display_name': name,
                    'is_active': True,
                    'updated_at': datetime.utcnow()
                }
            )
            await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Saved mapping for {phone[-4:]}")
        except Exception as e:
            logger.error(f"Save mapping failed: {e}")
            await self.db.rollback()
    
    async def build_context(
        self,
        person_id: str,
        phone: str
    ) -> Dict[str, Any]:
        """
        Build operational context for user.
        
        Args:
            person_id: MobilityOne person ID
            phone: Phone number
            
        Returns:
            Context dictionary
        """
        context = {
            "person_id": person_id,
            "phone": phone,
            "tenant_id": self.tenant_id,
            "display_name": "Korisnik",
            "vehicle": {}
        }
        
        if not self.gateway:
            return context
        
        try:
            from services.api_gateway import HttpMethod
            
            response = await self.gateway.execute(
                method=HttpMethod.GET,
                path="/automation/MasterData",
                params={"personId": person_id}
            )
            
            if response.success:
                data = response.data
                
                if isinstance(data, list):
                    data = data[0] if data else {}
                elif isinstance(data, dict) and "Data" in data:
                    data = data["Data"][0] if data["Data"] else {}
                
                context["vehicle"] = {
                    "id": data.get("Id") or data.get("VehicleId") or "",
                    "plate": data.get("LicencePlate") or data.get("Plate") or "",
                    "name": data.get("FullVehicleName") or "Vozilo",
                    "vin": data.get("VIN") or "",
                    "mileage": str(data.get("Mileage", "N/A"))
                }
                
                if data.get("Driver"):
                    context["display_name"] = self._extract_name({"DisplayName": data["Driver"]})
                    
        except Exception as e:
            logger.warning(f"Build context failed: {e}")
        
        return context
