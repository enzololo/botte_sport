import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL_ACTIVITES = "https://u-sport.univ-nantes.fr/activites"

# ===== TES CRÉNEAUX : ajoute ou enlève des lignes =====
# "encadrant" : texte préféré dans la ligne ("" = le premier disponible)
CRENEAUX = [
    {"activite": "volley", "jour": "lundi", "heure": "19:00", "encadrant": "Encadrant ETUDIANT"},
    {"activite": "volley", "jour": "vendredi", "heure": "18:00", "encadrant": "Encadrant ETUDIANT"},
]

# ===== OUVERTURE DES INSCRIPTIONS (heure de Paris) =====
OUVERTURE = "00:00"     # le robot attend cette heure pile avant de chercher les places
RETRY_MIN = 10          # puis réessaie pendant 10 minutes si les cases sont inactives
PAUSE_RETRY = 3         # secondes entre deux essais
# ======================================================

TZ = ZoneInfo("Europe/Paris")

MAJ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MIN = "abcdefghijklmnopqrstuvwxyz"

BOUTON = "(text()[contains(.,'Réserver')] or text()[contains(.,'Modifier')])"

XPATH_OK = "//*[normalize-space(text())='Ok' or normalize-space(text())='OK']"

XPATH_OUI = (
    "//*[self::button or self::a or @role='button']"
    "[normalize-space(.)='Oui' or normalize-space(.)='OUI']"
    " | //*[normalize-space(text())='Oui' or normalize-space(text())='OUI']"
)


class CreneauComplet(Exception):
    """Aucune case active : complet ou inscriptions pas encore ouvertes."""


def nom(c):
    return f"{c['activite']} {c['jour']} {c['heure']}"


def xpath_ligne(jour, heure):
    return (
        f"//tr[td[contains(translate(normalize-space(.), '{MAJ}', '{MIN}'), '{jour}')] "
        f"and td[contains(normalize-space(.), '{heure} -')]]"
    )


def nettoyer(texte):
    return " ".join((texte or "").split())


def heure_ouverture():
    h, m = map(int, OUVERTURE.split(":"))
    return datetime.now(TZ).replace(hour=h, minute=m, second=0, microsecond=0)


def attendre_ouverture():
    """Attend l'heure d'ouverture. Ne fait rien si elle est passée ou trop lointaine (test manuel)."""
    reste = (heure_ouverture() - datetime.now(TZ)).total_seconds()
    if reste <= 0:
        print("Heure d'ouverture déjà passée, on y va.")
        return
    if reste > 30 * 60:
        print(f"Ouverture dans plus de 30 min ({OUVERTURE}), pas d'attente (test manuel).")
        return
    print(f"Attente de l'ouverture à {OUVERTURE} (dans {int(reste)} s)...")
    while reste > 0:
        time.sleep(min(reste, 30))
        reste = (heure_ouverture() - datetime.now(TZ)).total_seconds()
    print("C'est l'heure !")


def ouvrir(driver, url):
    """Ouvre une page ; si le chargement traîne, on l'arrête et on continue."""
    try:
        driver.get(url)
    except TimeoutException:
        print("Chargement lent, on arrête le chargement et on continue.")
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass


def fermer_popup(driver, timeout=8):
    try:
        bouton = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, XPATH_OK))
        )
        driver.execute_script("arguments[0].click();", bouton)
        time.sleep(1)
        print("Popup d'information fermé.")
        return True
    except Exception:
        return False


def clic(driver, element):
    try:
        element.click()
    except Exception:
        fermer_popup(driver, timeout=1)
        try:
            element.click()
        except Exception:
            driver.execute_script("arguments[0].click();", element)


def defiler_jusqu(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(1)


def charger_tous_les_creneaux(driver):
    nb_precedent = -1
    for _ in range(6):
        lignes = driver.find_elements(By.XPATH, "//tr[td]")
        if not lignes:
            time.sleep(1)
            continue
        driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", lignes[-1])
        time.sleep(1)
        if len(lignes) == nb_precedent:
            break
        nb_precedent = len(lignes)
    print(f"{nb_precedent} créneaux visibles dans la fenêtre.")


def creneaux_cibles(driver, jour, heure):
    lignes = driver.find_elements(By.XPATH, xpath_ligne(jour, heure))
    if not lignes:
        return "", []

    def date_de(l):
        td = l.find_element(
            By.XPATH,
            f"./td[contains(translate(normalize-space(.), '{MAJ}', '{MIN}'), '{jour}')]",
        )
        return nettoyer(td.get_attribute("textContent"))

    date_cible = date_de(lignes[0])
    return date_cible, [l for l in lignes if date_de(l) == date_cible]


def reserver_creneau(driver, wait, c):
    """Réserve un créneau. Lève CreneauComplet si aucune case n'est active."""
    print(f"\n=== Créneau : {nom(c)} ===")

    ouvrir(driver, URL_ACTIVITES)
    fermer_popup(driver, timeout=8)
    time.sleep(2)

    xpath_carte = (
        f"//*[contains(translate(normalize-space(text()),'{MAJ}','{MIN}'),'{c['activite']}')]"
        f"/following::*[{BOUTON}][1]"
    )
    bouton_carte = wait.until(EC.presence_of_element_located((By.XPATH, xpath_carte)))
    defiler_jusqu(driver, bouton_carte)
    driver.execute_script("arguments[0].click();", bouton_carte)
    time.sleep(2)
    fermer_popup(driver, timeout=2)

    wait.until(EC.presence_of_element_located((By.XPATH, xpath_ligne(c["jour"], c["heure"]))))
    charger_tous_les_creneaux(driver)

    date_cible, lignes = creneaux_cibles(driver, c["jour"], c["heure"])
    print(f"{len(lignes)} créneau(x) trouvé(s) pour {date_cible} :")
    candidats = []
    for ligne in lignes:
        case = ligne.find_element(By.XPATH, ".//input[@type='checkbox']")
        coche = case.is_selected()
        print(f"  [{'x' if coche else ' '}] {nettoyer(ligne.get_attribute('textContent'))}")
        candidats.append((ligne, case, coche))

    # Déjà inscrit : rien à faire (cliquer désinscrirait)
    if any(coche for _, _, coche in candidats):
        print("ℹ️ Déjà inscrit à ce créneau, rien à faire.")
        return

    def priorite(x):
        texte = nettoyer(x[0].get_attribute("textContent")).lower()
        pref = c["encadrant"].lower()
        return 0 if pref and pref in texte else 1

    disponibles = sorted((x for x in candidats if x[1].is_enabled()), key=priorite)
    if not disponibles:
        raise CreneauComplet(f"{nom(c)} ({date_cible}) : aucune case active.")

    ligne, case, _ = disponibles[0]
    print(f"Créneau choisi : {nettoyer(ligne.get_attribute('textContent'))}")

    defiler_jusqu(driver, case)
    clic(driver, case)
    time.sleep(1)
    try:
        print(f"Case cochée après le clic : {case.is_selected()}")
    except Exception:
        pass

    print("Validation de la réservation...")
    bouton_oui = WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, XPATH_OUI)))
    clic(driver, bouton_oui)
    time.sleep(2)


def inscrire(driver, wait, etat):
    restants, reussis, complets = etat["restants"], etat["reussis"], etat["complets"]

    # Connexion (faite EN AVANCE, avant l'ouverture)
    print("Clic sur le bouton Connexion...")
    bouton_connexion = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Connexion')]"))
    )
    try:
        bouton_connexion.click()
    except TimeoutException:
        print("Chargement lent, on continue.")
        driver.execute_script("window.stop();")

    print("Saisie des identifiants...")
    wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(os.environ["U_USER"])
    driver.find_element(By.ID, "password").send_keys(os.environ["U_PASS"])
    try:
        driver.find_element(By.ID, "submitBtn").click()
    except TimeoutException:
        print("Connexion lente, on continue.")
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    time.sleep(3)
    fermer_popup(driver, timeout=5)

    # Attente de l'ouverture des inscriptions
    attendre_ouverture()

    # Premier passage : chaque créneau une fois
    while restants:
        c = restants[0]
        try:
            reserver_creneau(driver, wait, c)
            print(f"✅ Traité : {nom(c)}")
            reussis.append(c)
        except CreneauComplet as e:
            print(f"⛔ {e}")
            complets.append(c)
        restants.pop(0)

    # Réessais pendant RETRY_MIN minutes pour les créneaux sans case active
    fin_retry = heure_ouverture() + timedelta(minutes=RETRY_MIN)
    while complets and datetime.now(TZ) < fin_retry:
        print(f"\nRéessai dans {PAUSE_RETRY} s ({len(complets)} créneau(x) en attente)...")
        time.sleep(PAUSE_RETRY)
        for c in list(complets):
            try:
                reserver_creneau(driver, wait, c)
                print(f"✅ Traité : {nom(c)}")
                complets.remove(c)
                reussis.append(c)
            except CreneauComplet as e:
                print(f"⛔ {e}")


def reserver():
    max_tentatives = 3
    tentative_actuelle = 0
    etat = {"restants": list(CRENEAUX), "reussis": [], "complets": []}

    while tentative_actuelle < max_tentatives:
        tentative_actuelle += 1
        print(f"\n--- Tentative {tentative_actuelle} sur {max_tentatives} ---")

        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.page_load_strategy = 'eager'

        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 20)

        try:
            driver.set_page_load_timeout(60)
            ouvrir(driver, URL_ACTIVITES)
            inscrire(driver, wait, etat)
            break  # tout a été traité sans erreur technique

        except Exception as e:
            message = str(e).splitlines()[0] if str(e) else ""
            print(f"❌ Erreur lors de la tentative {tentative_actuelle} : {type(e).__name__} {message}")
            try:
                print(f"URL actuelle : {driver.current_url}")
                driver.save_screenshot(f"erreur_tentative_{tentative_actuelle}.png")
                with open(f"page_tentative_{tentative_actuelle}.html", "w", encoding="utf-8") as f:
                    f.write(driver.page_source)
            except Exception:
                pass

            if tentative_actuelle < max_tentatives:
                delai_attente = 5 * tentative_actuelle
                print(f"Attente de {delai_attente} secondes avant la prochaine tentative...")
                time.sleep(delai_attente)
            else:
                print("❌ Nombre maximum de tentatives atteint.")
        finally:
            driver.quit()

    print("\n===== Bilan =====")
    for c in etat["reussis"]:
        print(f"✅ {nom(c)}")
    for c in etat["complets"]:
        print(f"⛔ {nom(c)} (plus de place)")
        print(f"::error::Plus de place : {nom(c)}")
    for c in etat["restants"]:
        print(f"❌ {nom(c)} (échec technique)")

    return not etat["restants"] and not etat["complets"]


if __name__ == "__main__":
    if not os.environ.get("U_USER") or not os.environ.get("U_PASS"):
        print("❌ Variables U_USER et U_PASS manquantes (à définir dans les secrets GitHub).")
        sys.exit(1)
    sys.exit(0 if reserver() else 1)
