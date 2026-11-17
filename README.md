# Utilitaires CS2

Utilitaires CS2 transforme des démos GOTV `.dem` et des archives HLTV `.rar` en clips MP4 de lancers de grenades. L’interface s’ouvre dans le navigateur. Le serveur, les démos et les exports restent sur ton PC Windows.

## Setup

1. Récupère le dossier avec **Code → Download ZIP** sur GitHub et extrais-le, ou clone le dépôt avec Git.

   ```powershell
   git clone https://github.com/KozlovAntoine/cs2_demo.git
   cd cs2_demo
   ```

2. Installe Python 3.10 ou plus récent en 64 bits pour Windows, avec Tcl/Tk pour le sélecteur de fichiers. Rends la commande `python` disponible dans le `PATH`, puis vérifie-la avec `python --version` dans PowerShell. Avec l’installeur classique, l’option à cocher est **Add python.exe to PATH**. Les étapes 1 et 2 suffisent pour ouvrir le site et consulter une galerie existante. [Python pour Windows](https://www.python.org/downloads/windows/)
3. Pour générer des clips et afficher les cartes dans l’éditeur de zones, installe l’édition officielle de **CS Demo Manager 3.21.0** pour Windows. Ouvre-la une première fois, sélectionne **Embedded (recommended)** et clique sur **Connect** pour initialiser la base. [Version 3.21.0](https://github.com/akiver/cs-demo-manager/releases/tag/v3.21.0) · [Guide d’installation](https://cs-demo-manager.com/docs/installation)
4. Pour le rendu, installe **CS2** et laisse **Steam** ouvert et connecté. Dans les options de l’onglet **Video** d’un match dans CS Demo Manager, installe ou mets à jour **HLAE** et **FFmpeg**. Le bouton **Générer vidéo 9:16** utilise aussi FFmpeg. [Guide vidéo de CS Demo Manager](https://cs-demo-manager.com/docs/guides/video#installingupdating-required-software)
5. Pour ouvrir des archives `.rar`, installe **7-Zip** dans le dossier proposé par l’installeur, ou ajoute son dossier au `PATH`. [7-Zip](https://www.7-zip.org/)
6. Pour afficher les commandes de lancer et copier les positions et angles dans la console CS2, exécute cette commande depuis le dossier du projet. Le lecteur est facultatif pour générer les clips. [Demoparser](https://github.com/LaihoE/demoparser)

   ```powershell
   python -m pip install --target .local/throw-parser demoparser2==0.42.2 pandas
   ```

## Lancer le site

Double-clique sur `Lancer.cmd` dans l’Explorateur, ou lance cette commande dans PowerShell depuis le dossier du projet.

```powershell
python utility_app.py
```

La **Vidéothèque** s’ouvre dans ton navigateur à l’adresse [http://127.0.0.1:38473](http://127.0.0.1:38473). Si le navigateur ne s’ouvre pas, saisis cette adresse manuellement. Utilise **Quitter** dans le site ou **Ctrl+C** dans le terminal pour arrêter le serveur.

Si CS Demo Manager est installé dans un dossier personnalisé, indique le dossier contenant `cs-demo-manager.exe` avec `--csdm`. L’exemple ci-dessous suppose que ce logiciel est dans un dossier voisin. Si le port par défaut est occupé, ajoute `--port 38474` et ouvre l’adresse affichée par la commande.

```powershell
python utility_app.py --csdm "../CS Demo Manager"
```

## Utiliser le site

Dans **Nouvel export**, clique sur **Parcourir…** et sélectionne les `.dem` ou `.rar` à traiter. Maintiens **Ctrl** pour choisir plusieurs fichiers. Choisis le type d’utilitaire, puis clique sur **Générer les clips**. Avec une seule archive sélectionnée, tu peux choisir une démo de l’archive. Avec plusieurs fichiers, toutes les démos de chaque archive sont traitées. **Galeries** donne accès aux exports individuels terminés.

La **Vidéothèque** regroupe les exports par carte. Le radar affiche les points d’activation par défaut. Le menu **Points** permet d’afficher les positions de départ. Un point numéroté regroupe des lancers proches. Clique sur un repère pour isoler ses clips et afficher leurs départs ou activations reliés par des pointillés. Clique sur le fond du radar pour retrouver tous les repères et la liste filtrée complète. Le bouton **Supprimer** efface le MP4 sélectionné après confirmation et met à jour le catalogue.

Avant la capture, chaque lancer est comparé aux clips de la même carte et du même type, y compris ceux des exports précédents. Un lancer est ignoré si un même clip correspond à la fois au groupement de départ et à celui d’activation. Le journal indique les doublons ignorés. Supprimer le dernier clip correspondant permet à un prochain import de retenir ce lancer à nouveau.

La vidéothèque compte toutes les occurrences de chaque lancer dans les démos traitées, y compris les doublons ignorés. La moyenne porte sur les parties où le lancer a été observé. Réimporter une démo met à jour son compteur. Le menu **Fréquence** filtre les badges **Rare** (moins de 2), **Commun** (de 2 à 10) et **Très fréquent** (plus de 10). Les seuils `RARE_BELOW` et `COMMON_MAX` dans `gallery/gallery.js` s’appliquent dès l’actualisation de la page. Les clips existants obtiennent une fréquence dès qu’une nouvelle analyse retrouve leur lancer.

Les copies de travail des démos et les fichiers de capture intermédiaires sont nettoyés automatiquement après chaque capture et en cas d’échec. Les `.dem` ou `.rar` d’origine servent aux prochains exports. Les clips restent lisibles depuis leurs MP4.

Dans la vidéothèque, sélectionne un lancer et clique sur **Générer vidéo 9:16**. FFmpeg recadre le centre du MP4 existant en 720 × 1280 en conservant les proportions et le son. Après le rendu, **Ouvrir le dossier** donne accès au MP4. Le nom contient le joueur, la carte, le type d’utilitaire et la date du rendu au format `AAAA-MM-JJ HH-MM`.

Clique sur l’étoile sous la vidéo pour ajouter le clip sélectionné à tes favoris. Les filtres **Favoris** et **À traiter** s’appliquent à la liste et au radar. Un clic sur une tuile marque le clip comme **Vu**. Les états sont enregistrés dans la vidéothèque locale.

L’URL de la vidéothèque mémorise la carte, le clip, le groupement radar et les filtres. Utilise Retour ou Avancer dans le navigateur pour retrouver une sélection, ou copie l’adresse pour la rouvrir sur ce PC. **Favoris** et **À traiter** s’activent l’un à la place de l’autre.

Les deux radars, dans la vidéothèque et dans l’éditeur, se zooment à la molette ou par pas de 10 % avec **−** et **+**. Une fois le radar agrandi, fais glisser le fond pour te déplacer. La main ouverte indique que tu peux déplacer la carte et se ferme pendant le glissement.

Dans la liste des lancers, le titre indique le callout à la position d’activation et le joueur apparaît en dessous. Clique sur **Éditer les zones** pour choisir une carte et un niveau, puis sur **Nouvelle zone**. Pose les sommets sur le radar et reclique sur le premier pour fermer le polygone. Sur un tracé fermé ou une zone sélectionnée, fais glisser les sommets, puis clique sur **Enregistrer**. Tu peux aussi renommer ou supprimer la zone sélectionnée. Dans **Superpositions**, glisse la zone sélectionnée et celles qui la chevauchent pour changer leur ordre. La position 1 passe devant les autres et l’ordre est enregistré au dépôt. Lorsqu’une activation est couverte par plusieurs zones, le premier callout de la liste qui la couvre s’affiche. Hors zone, la liste affiche **À définir**. [Guide des callouts sur Steam](https://steamcommunity.com/sharedfiles/filedetails/?l=french&id=2721539148)

Les commandes détectées autour du lancer s’affichent à droite de **Supprimer**. Les deux boutons colorés indiquent un lancer à mi-puissance. **Console CS2 → Copier** copie la position et la visée à coller dans la console sur la même carte en entraînement (`sv_cheats 1`). Pour un saut, la position copiée correspond au dernier appui au sol lorsqu’il est visible dans la démo.

## Retrouver ou partager une galerie

Les galeries, les MP4, les favoris et les zones de callout sont conservés dans `exports/`. Pour déplacer toute ta vidéothèque vers une autre copie du projet, copie ce dossier en entier. Tu peux aussi indiquer un autre dossier au lancement, par exemple `python utility_app.py --output ../mes-exports`.

Pour partager une galerie individuelle, envoie son sous-dossier complet dans `exports/`. La personne qui la reçoit peut ouvrir son `index.html` pour consulter les clips dans son navigateur. Les exports et les démos restent en dehors du dépôt Git.

## Découpage et radar

Le rendu commence en POV du lanceur trois secondes avant le lancer et suit le projectile jusqu’à son activation. La caméra reste sur l’effet trois secondes. Si la démo ne fournit pas d’événement d’activation, la galerie indique une fin estimée à partir de la disparition du projectile ou du dernier tick observé. Les limites au début et à la fin du fichier peuvent raccourcir un clip.

La caméra suit les positions enregistrées dans la démo avec un recul fixe. Elle peut traverser un mur dans un espace étroit. Les cartes sans radar compatible restent consultables par la liste des lancers.

Les calibrations de `radars.json` et les images du radar proviennent de CS Demo Manager. [Calibrations de CS Demo Manager](https://github.com/akiver/cs-demo-manager/blob/v3.21.0/src/node/database/maps/default-maps.mts)
